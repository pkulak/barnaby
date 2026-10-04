"""Barnaby conversation integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BarnabyAuthError,
    BarnabyClient,
    BarnabyConnectionError,
    BarnabyResponseError,
)
from .const import CONF_TOKEN

PLATFORMS = (Platform.CONVERSATION,)

type BarnabyConfigEntry = ConfigEntry[BarnabyClient]


async def async_setup_entry(hass: HomeAssistant, entry: BarnabyConfigEntry) -> bool:
    """Set up Barnaby from a config entry."""
    client = BarnabyClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
    )

    try:
        await client.async_status()
    except BarnabyAuthError as err:
        raise ConfigEntryAuthFailed from err
    except (BarnabyConnectionError, BarnabyResponseError) as err:
        raise ConfigEntryNotReady from err

    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: BarnabyConfigEntry) -> bool:
    """Unload Barnaby."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
