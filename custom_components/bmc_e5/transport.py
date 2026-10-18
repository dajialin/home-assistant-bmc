"""Bluetooth connection loop with serialized, non-retrying user operations."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone

from .protocol import DATA_UUID, SERVICE_UUID, FrameBuffer, ProtocolError, ReadOnlySession, parse_realtime
from .control import ControlTransaction


async def run_connection(connect, profile, on_message, stop, *, validation_only=False, control_queue=None):
    session = ReadOnlySession(*profile)
    frames = FrameBuffer()
    queue = asyncio.Queue(maxsize=512)
    overflow = False
    active = None
    result = None
    control_deadline = None
    client = await connect()

    def notify(_, data):
        nonlocal overflow
        try:
            queue.put_nowait(bytes(data))
        except asyncio.QueueFull:
            overflow = True

    try:
        service = client.services.get_service(SERVICE_UUID)
        characteristic = service.get_characteristic(DATA_UUID) if service else None
        if not characteristic or not {"notify", "write-without-response"}.issubset(characteristic.properties):
            raise ProtocolError("Unsupported GATT services")

        async def send(data):
            size = max(1, min(98, client.mtu_size - 5, characteristic.max_write_without_response_size))
            for offset in range(0, len(data), size):
                await client.write_gatt_char(characteristic, data[offset:offset + size], response=False)
                await asyncio.sleep(0.02)

        async def send_operation(operation):
            command, payload = operation
            if command not in (0x81D0, 0x8194, 0x81C0, 0x82BA, 0x82B8):
                raise ProtocolError("Control operation not allowed")
            if session.state != "ready":
                raise ProtocolError("Device session is not ready")
            await send(session.codec._encode(command, 0, 0, payload))

        await client.start_notify(characteristic, notify)
        await send(session.begin())
        loop = asyncio.get_running_loop()
        last_realtime = loop.time()
        deadline = loop.time() + 25
        heartbeat = loop.time() + 10
        while not stop.is_set():
            if active is not None:
                if result.cancelled():
                    raise ProtocolError("Control cancelled; device outcome may be unknown")
                if loop.time() >= control_deadline:
                    raise TimeoutError("Control acknowledgement/readback timed out; no automatic retry")
            elif (control_queue is not None and not control_queue.empty() and session.state == "ready"
                  and queue.empty() and not frames.pending):
                key, value, result = control_queue.get_nowait()
                if not result.cancelled():
                    active = ControlTransaction(key, value)
                    control_deadline = loop.time() + 25
                    await send_operation(active.begin())
            if not client.is_connected or overflow:
                raise ProtocolError("Bluetooth connection interrupted")
            if loop.time() >= deadline and session.state != "ready":
                raise TimeoutError("Handshake timed out")
            if loop.time() - last_realtime > 15:
                raise TimeoutError("Realtime data expired")
            try:
                data = await asyncio.wait_for(queue.get(), 1)
            except TimeoutError:
                data = b""
            for frame in frames.feed(data):
                message, replies = session.receive(frame)
                for reply in replies:
                    await send(reply)
                if message.command == 0x06EA:
                    parse_realtime(message)
                    last_realtime = loop.time()
                if session.state == "ready":
                    on_message(message, datetime.now(timezone.utc))
                    if active is not None:
                        if result.cancelled():
                            raise ProtocolError("Control cancelled")
                        for operation in active.receive(message):
                            await send_operation(operation)
                        if active.done:
                            result.set_result(None)
                            active = None
                            result = None
                    if validation_only and message.command == 0x06EA:
                        return
            if loop.time() >= heartbeat and session.state in ("reading", "ready"):
                await send(session.heartbeat())
                heartbeat = loop.time() + 10
    finally:
        if result is not None and not result.done():
            result.set_exception(ProtocolError("Control not confirmed; inspect device before retrying"))
        if control_queue is not None:
            while not control_queue.empty():
                _, _, pending = control_queue.get_nowait()
                if not pending.done():
                    pending.set_exception(ProtocolError("Device disconnected; control was not queued for reconnect"))
        # Cancellation must release the GATT connection before reloading/unloading.
        with suppress(Exception):
            await asyncio.wait_for(client.disconnect(), timeout=10)
