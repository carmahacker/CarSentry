"""Config flow for CarSentry — prefers Supervisor addon discovery."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CarSentryApi
from .const import (
    CONF_BASE_URL,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HOST, default=""): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


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
        errors = {}
        session = async_get_clientsession(self.hass)

        # Auto-discovery candidates (no manual port needed when possible)
        candidates = [
            # Supervisor ingress / internal addon network
            "http://carsentry:8000",
            "http://local-carsentry:8000",
            "http://a0d7b954-carsentry:8000",
            # Common host mappings
            "http://127.0.0.1:8010",
            "http://localhost:8010",
            "http://homeassistant.local:8010",
            "http://homeassistant:8010",
        ]

        if user_input is None:
            # Try auto-discovery first
            for url in candidates:
                if await _try_url(session, url):
                    await self.async_set_unique_id("carsentry")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="CarSentry",
                        data={CONF_BASE_URL: url},
                    )
            # Show form if nothing found
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={},
                description_placeholders={
                    "hint": "Addon не найден автоматически. Укажите host/port или проверьте, что CarSentry Addon запущен."
                },
            )

        # Manual path
        host = (user_input.get(CONF_HOST) or "").strip()
        port = user_input.get(CONF_PORT) or DEFAULT_PORT

        if host:
            base_url = f"http://{host}:{port}"
        else:
            # empty host → try defaults again with given port
            base_url = f"http://127.0.0.1:{port}"

        if await _try_url(session, base_url):
            await self.async_set_unique_id("carsentry")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="CarSentry",
                data={CONF_BASE_URL: base_url, CONF_HOST: host, CONF_PORT: port},
            )

        errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
