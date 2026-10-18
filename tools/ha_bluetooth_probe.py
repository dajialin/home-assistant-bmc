"""Read HA Bluetooth subscriptions only; never send device commands."""
import argparse
import getpass
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import websocket


def endpoint(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an HTTPS HA URL without credentials")
    if parts.query or parts.fragment:
        raise ValueError("URL must not contain a query or fragment")
    return urlunsplit(("wss", parts.netloc, parts.path.rstrip("/") + "/api/websocket", "", ""))


def capture(url, token, seconds):
    ws = websocket.create_connection(endpoint(url), timeout=15)
    messages = []
    try:
        if json.loads(ws.recv()).get("type") != "auth_required":
            raise RuntimeError("Unexpected HA authentication handshake")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            raise RuntimeError("HA authentication failed")
        for index, command in enumerate(("bluetooth/subscribe_scanner_details",
                                          "bluetooth/subscribe_advertisements",
                                          "bluetooth/subscribe_connection_allocations"), 1):
            ws.send(json.dumps({"id": index, "type": command}))
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ws.settimeout(max(0.01, end - time.monotonic()))
            try:
                message = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                break
            messages.append(message)
    finally:
        ws.close()
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": seconds, "messages": messages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--seconds", type=int, default=20, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")
    endpoint(args.url)
    token = os.environ.get("HA_TOKEN") or getpass.getpass("HA token (not saved): ")
    data = capture(args.url, token, args.seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    errors = [m.get("error") for m in data["messages"] if m.get("success") is False]
    print(json.dumps({"saved": str(args.output), "message_count": len(data["messages"]), "errors": errors}))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
