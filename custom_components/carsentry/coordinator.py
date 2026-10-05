"""DataUpdateCoordinator for CarSentry."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CarSentryApi
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CarSentryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches cameras, parking cars and recent events."""

    def __init__(self, hass: HomeAssistant, api: CarSentryApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            health = await self.api.health()
            cameras = await self.api.get_cameras()
            parking = await self.api.get_parking_cars()
            events = await self.api.get_events(limit=10)
            return {
                "health": health,
                "cameras": cameras,
                "parking_cars": parking,
                "events": events,
            }
        except Exception as err:
            raise UpdateFailed(f"Error communicating with CarSentry: {err}") from err
