"""CarSentry integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, CONF_HOST, CONF_PORT, CONF_BASE_URL
from .coordinator import CarSentryCoordinator
from .api import CarSentryApi

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CarSentry from a config entry."""
    # Prefer explicit base_url (supervisor/ingress), else host:port
    base_url = entry.data.get(CONF_BASE_URL)
    if not base_url:
        host = entry.data.get(CONF_HOST, "localhost")
        port = entry.data.get(CONF_PORT, 8010)
        base_url = f"http://{host}:{port}"

    session = async_get_clientsession(hass)
    api = CarSentryApi(session, base_url)

    coordinator = CarSentryCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # --- Services ---
    async def handle_discover_rtsp(call):
        host_ = call.data.get("host")
        username = call.data.get("username", "")
        password = call.data.get("password", "")
        port_ = call.data.get("port", 554)
        result = await api.discover_rtsp(host_, username, password, port_)
        hass.bus.async_fire("carsentry_rtsp_discover_result", result)

    async def handle_verify_rtsp(call):
        url = call.data.get("rtsp_url")
        result = await api.verify_rtsp(url)
        hass.bus.async_fire("carsentry_rtsp_verify_result", result)

    hass.services.async_register(DOMAIN, "discover_rtsp", handle_discover_rtsp)
    hass.services.async_register(DOMAIN, "verify_rtsp", handle_verify_rtsp)

    # --- Poll HA cameras (source_type=ha_camera) and send frames to addon ---
    async def _poll_ha_cameras(_now=None):
        cameras = coordinator.data.get("cameras") or []
        for cam in cameras:
            if not cam.get("enabled"):
                continue
            if (cam.get("source_type") or "rtsp") != "ha_camera":
                continue
            entity_id = (cam.get("ha_entity_id") or "").strip()
            if not entity_id:
                continue
            try:
                image = await async_get_image(hass, entity_id)
                if image and image.content:
                    await api.analyze_frame(cam["id"], image.content)
            except Exception as err:
                _LOGGER.debug(
                    "HA camera poll failed for %s (cam %s): %s",
                    entity_id,
                    cam.get("id"),
                    err,
                )

    unsub = async_track_time_interval(
        hass, _poll_ha_cameras, timedelta(seconds=5)
    )
    entry.async_on_unload(unsub)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
