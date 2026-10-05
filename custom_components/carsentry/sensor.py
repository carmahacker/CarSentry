"""Sensor platform for CarSentry — dynamic parking cars + last plate."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
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

    known_cars: set[int] = set()
    last_plate_added = False

    @callback
    def _sync_entities() -> None:
        nonlocal last_plate_added
        new: list[SensorEntity] = []

        for car in coordinator.data.get("parking_cars") or []:
            cid = car["id"]
            if cid in known_cars:
                continue
            known_cars.add(cid)
            new.append(
                ParkingCarSensor(
                    coordinator,
                    cid,
                    car.get("name") or car.get("plate") or f"Car {cid}",
                )
            )

        if not last_plate_added:
            last_plate_added = True
            new.append(LastPlateSensor(coordinator))

        if new:
            async_add_entities(new)

    _sync_entities()
    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))


class ParkingCarSensor(CoordinatorEntity[CarSentryCoordinator], SensorEntity):
    """Presence state of a known parking car (home / away)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:car"

    def __init__(
        self, coordinator: CarSentryCoordinator, car_id: int, name: str
    ) -> None:
        super().__init__(coordinator)
        self._car_id = car_id
        self._attr_unique_id = f"carsentry_car_{car_id}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "carsentry")},
            "name": "CarSentry",
            "manufacturer": "CarSentry",
        }

    def _car(self) -> dict | None:
        for c in self.coordinator.data.get("parking_cars") or []:
            if c["id"] == self._car_id:
                return c
        return None

    @property
    def available(self) -> bool:
        return self._car() is not None

    @property
    def native_value(self) -> str | None:
        car = self._car()
        return car["state"] if car else None

    @property
    def extra_state_attributes(self) -> dict:
        car = self._car() or {}
        return {
            "plate": car.get("plate"),
            "last_seen_at": car.get("last_seen_at"),
            "home_value": car.get("home_value"),
            "away_value": car.get("away_value"),
            "absence_timeout": car.get("absence_timeout"),
            "enabled": car.get("enabled"),
        }


class LastPlateSensor(CoordinatorEntity[CarSentryCoordinator], SensorEntity):
    """Last recognized plate number."""

    _attr_has_entity_name = True
    _attr_name = "Last plate"
    _attr_icon = "mdi:card-account-details"
    _attr_unique_id = "carsentry_last_plate"

    def __init__(self, coordinator: CarSentryCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "carsentry")},
            "name": "CarSentry",
            "manufacturer": "CarSentry",
        }

    @property
    def native_value(self) -> str | None:
        events = self.coordinator.data.get("events") or []
        if not events:
            return None
        return events[0].get("plate")

    @property
    def extra_state_attributes(self) -> dict:
        events = self.coordinator.data.get("events") or []
        if not events:
            return {}
        e = events[0]
        return {
            "camera_id": e.get("camera_id"),
            "timestamp": e.get("timestamp"),
            "detector_confidence": e.get("detector_confidence"),
            "ocr_confidence": e.get("ocr_confidence"),
        }
