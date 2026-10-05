"""HTTP client for CarSentry addon API."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class CarSentryApi:
    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")

    async def _get(self, path: str) -> Any:
        async with self._session.get(f"{self._base}{path}", timeout=15) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, json: dict | None = None) -> Any:
        async with self._session.post(
            f"{self._base}{path}", json=json or {}, timeout=30
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def health(self) -> dict:
        return await self._get("/api/health")

    async def get_cameras(self) -> list:
        return await self._get("/api/cameras")

    async def get_parking_cars(self) -> list:
        return await self._get("/api/parking-cars")

    async def get_events(self, limit: int = 20) -> list:
        return await self._get(f"/api/events?limit={limit}")

    async def get_anpr_settings(self) -> dict:
        return await self._get("/api/anpr")

    async def discover_rtsp(
        self,
        host: str,
        username: str = "",
        password: str = "",
        port: int = 554,
    ) -> dict:
        return await self._post(
            "/api/rtsp/discover",
            {
                "host": host,
                "username": username,
                "password": password,
                "port": port,
            },
        )

    async def verify_rtsp(self, rtsp_url: str) -> dict:
        return await self._post("/api/rtsp/verify", {"rtsp_url": rtsp_url})

    async def get_camera_snapshot(self, camera_id: int) -> bytes | None:
        async with self._session.get(
            f"{self._base}/api/cameras/{camera_id}/snapshot", timeout=15
        ) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.read()

    async def analyze_frame(self, camera_id: int, image_bytes: bytes) -> dict:
        """Send JPEG bytes from an HA camera to the addon for ANPR."""
        form = aiohttp.FormData()
        form.add_field(
            "file",
            image_bytes,
            filename="frame.jpg",
            content_type="image/jpeg",
        )
        async with self._session.post(
            f"{self._base}/api/cameras/{camera_id}/analyze",
            data=form,
            timeout=60,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
