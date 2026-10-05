"""
RTSP auto-discovery for common IP camera vendors.
Tries several standard URL patterns and verifies that a real frame can be read.
No vendor lock-in (Dahua, Hikvision, Reolink, Uniview, generic, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import cv2

log = logging.getLogger("carsentry.rtsp")

# Common RTSP path patterns (path only, credentials + host injected later)
COMMON_PATHS = [
    # Dahua / Amcrest style
    "/Streaming/Channels/101",
    "/Streaming/Channels/1",
    "/cam/realmonitor?channel=1&subtype=0",
    "/cam/realmonitor?channel=1&subtype=1",
    # Hikvision style
    "/Streaming/Channels/101",
    "/h264/ch1/main/av_stream",
    "/ISAPI/Streaming/channels/101",
    # Reolink
    "/h264Preview_01_main",
    "/h264Preview_01_sub",
    # Uniview / generic
    "/media/video1",
    "/live/ch00_0",
    "/live0.264",
    "/stream1",
    "/stream0",
    "/1",
    "/11",
    "/12",
]


@dataclass
class DiscoveryResult:
    success: bool
    rtsp_url: Optional[str] = None
    path: Optional[str] = None
    message: str = ""
    tried: List[str] = None

    def __post_init__(self):
        if self.tried is None:
            self.tried = []


def _build_url(host: str, port: int, username: str, password: str, path: str) -> str:
    user = quote(username or "", safe="")
    pwd = quote(password or "", safe="")
    auth = f"{user}:{pwd}@" if user or pwd else ""
    # Ensure path starts with /
    if not path.startswith("/"):
        path = "/" + path
    return f"rtsp://{auth}{host}:{port}{path}"


def _try_open(url: str, timeout_sec: float = 4.0) -> bool:
    """
    Open RTSP with OpenCV/FFmpeg and try to read one frame.
    Must succeed on a real frame, not just TCP connect.
    """
    cap = None
    try:
        # CAP_FFMPEG + short open timeout via env would be ideal,
        # but we rely on a hard timeout around the whole call.
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap or not cap.isOpened():
            return False
        ok, frame = cap.read()
        return bool(ok and frame is not None and getattr(frame, "size", 0) > 0)
    except Exception as exc:
        log.debug("RTSP try failed for %s: %s", url, exc)
        return False
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


async def discover_rtsp(
    host: str,
    username: str = "",
    password: str = "",
    port: int = 554,
    extra_paths: Optional[List[str]] = None,
    timeout_per_try: float = 4.0,
) -> DiscoveryResult:
    """
    Try common RTSP paths (and optional extra_paths).
    Returns the first URL that successfully yields a frame.
    """
    host = (host or "").strip()
    if not host:
        return DiscoveryResult(success=False, message="Host/IP не указан")

    paths = list(dict.fromkeys(COMMON_PATHS + (extra_paths or [])))  # unique, preserve order
    tried: List[str] = []

    for path in paths:
        url = _build_url(host, port, username, password, path)
        # Mask password in logs / response
        safe_url = _build_url(host, port, username, "***" if password else "", path)
        tried.append(safe_url)

        log.info("Trying RTSP: %s", safe_url)
        ok = await asyncio.to_thread(_try_open, url, timeout_per_try)
        if ok:
            return DiscoveryResult(
                success=True,
                rtsp_url=url,
                path=path,
                message="RTSP успешно определён",
                tried=tried,
            )

    return DiscoveryResult(
        success=False,
        message="Автоматически определить RTSP не удалось. Укажите URL вручную.",
        tried=tried,
    )


async def verify_rtsp(url: str, timeout_sec: float = 5.0) -> DiscoveryResult:
    """Verify an arbitrary RTSP URL by reading one frame."""
    url = (url or "").strip()
    if not url:
        return DiscoveryResult(success=False, message="URL пустой")

    ok = await asyncio.to_thread(_try_open, url, timeout_sec)
    if ok:
        return DiscoveryResult(success=True, rtsp_url=url, message="Поток открывается, кадр получен")
    return DiscoveryResult(
        success=False,
        rtsp_url=url,
        message="Не удалось открыть поток или прочитать кадр",
    )
