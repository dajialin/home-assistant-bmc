"""UI setup with an actual authenticated read before creating an entry."""
import asyncio
import re
import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN, PROFILE_FILENAME
from .coordinator import connection_factory
from .profile import load_profile
from .transport import run_connection


class BmcConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_bluetooth(self, discovery_info):
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")
        self._discovered_address = discovery_info.address.upper()
        await self.async_set_unique_id(self._discovered_address)
        self._abort_if_unique_id_configured()
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        errors = {}
        address = getattr(self, "_discovered_address", "")
        if user_input is not None:
            address = user_input["address"].strip().upper()
            if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
                errors["address"] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                try:
                    profile = await self.hass.async_add_executor_job(load_profile, self.hass.config.path(PROFILE_FILENAME))
                except (OSError, ValueError, KeyError):
                    errors["base"] = "profile_missing"
                else:
                    try:
                        async with asyncio.timeout(55):
                            await run_connection(connection_factory(self.hass, address), profile,
                                                 lambda *_: None, asyncio.Event(), validation_only=True)
                    except Exception:
                        errors["base"] = "cannot_connect"
                    else:
                        return self.async_create_entry(title="BMC", data={"address": address})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required("address", default=address): str}), errors=errors)
