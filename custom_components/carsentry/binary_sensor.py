"""Binary sensors for camera connectivity — dynamic."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CarSentryCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: CarSentryCoordinator = data["coordinator"]

    known: set[int] = set()

    @callback
    def _sync() -> None:
        new = []
        for cam in coordinator.data.get("cameras") or []:
            cid = cam["id"]
            if cid in known:
                continue
            known.add(cid)
            new.append(
                CameraStatusBinarySensor(
                    coordinator,
                    cid,
                    cam.get("name") or f"Camera {cid}",
                )
            )
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class CameraStatusBinarySensor(
    CoordinatorEntity[CarSentryCoordinator], BinarySensorEntity
):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CarSentryCoordinator, camera_id: int, name: str
    ) -> None:
        super().__init__(coordinator)
        self._camera_id = camera_id
        self._attr_unique_id = f"carsentry_camera_{camera_id}_status"
        self._attr_name = f"{name} connected"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "carsentry")},
            "name": "CarSentry",
            "manufacturer": "CarSentry",
        }

    def _cam(self) -> dict | None:
        for c in self.coordinator.data.get("cameras") or []:
            if c["id"] == self._camera_id:
                return c
        return None

    @property
    def available(self) -> bool:
        return self._cam() is not None

    @property
    def is_on(self) -> bool:
        cam = self._cam()
        return bool(cam and cam.get("status") == "connected")

    @property
    def extra_state_attributes(self) -> dict:
        cam = self._cam() or {}
        return {
            "rtsp_url": cam.get("rtsp_url"),
            "purpose": cam.get("purpose"),
            "source_type": cam.get("source_type"),
            "ha_entity_id": cam.get("ha_entity_id"),
            "mode": cam.get("mode"),
            "enabled": cam.get("enabled"),
            "status": cam.get("status"),
        }
