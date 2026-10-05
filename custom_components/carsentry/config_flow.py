"""Config flow for CarSentry — auto-discovers addon, host/port optional."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CarSentryApi
from .const import CONF_BASE_URL, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HOST): str,
        vol.Optional(CONF_PORT): int,
    }
)


def _candidates(host: str | None = None, port: int | None = None) -> list[str]:
    urls: list[str] = []
    if host:
        p = port or DEFAULT_PORT
        urls.append(f"http://{host.strip()}:{p}")
    urls.extend(
        [
            "http://carsentry:8000",
            "http://local-carsentry:8000",
            "http://a0d7b954-carsentry:8000",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8010",
            "http://localhost:8000",
            "http://localhost:8010",
            "http://homeassistant.local:8010",
            "http://homeassistant:8010",
        ]
    )
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def _try_url(session, url: str) -> bool:
    try:
        api = CarSentryApi(session, url)
        health = await api.health()
        return bool(health.get("ok", True))
    except Exception:
        return False


class CarSentryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CarSentry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        session = async_get_clientsession(self.hass)

        if user_input is None:
            for url in _candidates():
                if await _try_url(session, url):
                    await self.async_set_unique_id(DOMAIN)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="CarSentry",
                        data={CONF_BASE_URL: url},
                    )
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={},
            )

        host = (user_input.get(CONF_HOST) or "").strip() or None
        port = user_input.get(CONF_PORT)

        for url in _candidates(host, port):
            if await _try_url(session, url):
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                data = {CONF_BASE_URL: url}
                if host:
                    data[CONF_HOST] = host
                    data[CONF_PORT] = port or DEFAULT_PORT
                return self.async_create_entry(title="CarSentry", data=data)

        errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
