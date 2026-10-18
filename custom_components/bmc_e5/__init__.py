"""BMC E5 integration with bounded, explicitly requested controls."""


async def async_setup_entry(hass, entry):
    if getattr(entry, "title", None) in ("BMC E5", "BMC E5 B25A Plus"):
        hass.config_entries.async_update_entry(entry, title="BMC")
    from homeassistant.exceptions import ConfigEntryNotReady
    from .const import PLATFORMS, PROFILE_FILENAME
    from .coordinator import BmcCoordinator
    from .profile import load_profile
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)
    obsolete = registry.async_get_entity_id("sensor", "bmc_e5", f"{entry.data['address']}_last_update")
    if obsolete and registry.async_get(obsolete).config_entry_id == entry.entry_id:
        registry.async_remove(obsolete)
    try:
        profile = await hass.async_add_executor_job(load_profile, hass.config.path(PROFILE_FILENAME))
    except (OSError, ValueError, KeyError) as err:
        raise ConfigEntryNotReady("Local BMC protocol profile missing or invalid") from err
    coordinator = BmcCoordinator(hass, entry, profile)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start()
    return True


async def async_unload_entry(hass, entry):
    from .const import PLATFORMS
    # Stop first, including pending GATT operations; restore only if unload fails.
    await entry.runtime_data.stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        import asyncio
        entry.runtime_data._stop = asyncio.Event()
        entry.runtime_data.start()
    return unloaded
