"""Diagnostics support for the Codex integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_TOKEN

TO_REDACT = {
    CONF_API_TOKEN,
    "Authorization",
    "api_token",
    "details",
    "message",
    "prompt",
    "qr_url",
    "user_code",
    "verification_url",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator_data = None
    if runtime_data is not None:
        coordinator_data = runtime_data.coordinator.data

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "coordinator_data": coordinator_data,
        },
        TO_REDACT,
    )
