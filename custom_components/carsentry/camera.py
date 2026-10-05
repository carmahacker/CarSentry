"""Camera platform — last annotated plate snapshot per monitor camera."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CarSentryCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: CarSentryCoordinator = data["coordinator"]
    api = data["api"]

    known: set[int] = set()

    def _cameras() -> list[dict]:
        return coordinator.data.get("cameras") or []

    @callback
    def _add_new() -> None:
        new = []
        for cam in _cameras():
            cid = cam["id"]
            if cid in known:
                continue
            known.add(cid)
            new.append(
                CarSentryCamera(
                    coordinator,
                    api,
                    cid,
                    cam.get("name") or f"Camera {cid}",
                )
            )
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class CarSentryCamera(CoordinatorEntity[CarSentryCoordinator], Camera):
    """Exposes last annotated snapshot from CarSentry camera."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarSentryCoordinator,
        api: Any,
        camera_id: int,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._api = api
        self._camera_id = camera_id
        self._attr_unique_id = f"carsentry_cam_{camera_id}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "carsentry")},
            "name": "CarSentry",
            "manufacturer": "CarSentry",
        }
        self._last_image: bytes | None = None

    def _cam(self) -> dict | None:
        for c in self.coordinator.data.get("cameras") or []:
            if c["id"] == self._camera_id:
                return c
        return None

    @property
    def is_on(self) -> bool:
        cam = self._cam()
        return bool(cam and cam.get("status") == "connected")

    @property
    def extra_state_attributes(self) -> dict:
        cam = self._cam() or {}
        return {
            "purpose": cam.get("purpose"),
            "source_type": cam.get("source_type"),
            "ha_entity_id": cam.get("ha_entity_id"),
            "status": cam.get("status"),
            "mode": cam.get("mode"),
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        try:
            data = await self._api.get_camera_snapshot(self._camera_id)
            if data:
                self._last_image = data
            return self._last_image
        except Exception as err:
            _LOGGER.debug("Snapshot fetch failed for cam %s: %s", self._camera_id, err)
            return self._last_image
