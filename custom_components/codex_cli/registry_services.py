"""Explicit admin-only metadata services for Core 2026.9.4 registries.

All mutations/read-backs run synchronously inside the HA event loop. Registries
own their persistence; this module never opens files or loads a registry again.
"""
from __future__ import annotations

import asyncio
from enum import Enum
import logging
import re
import unicodedata

from homeassistant.const import EntityCategory, __version__ as HA_VERSION
from homeassistant.core import SupportsResponse
from homeassistant.helpers import area_registry as ar, floor_registry as fr
from homeassistant.helpers import entity_registry as er, device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SERVICES = (
    "area_get", "area_create", "area_update", "area_set_temperature_entity",
    "floor_get", "floor_create", "floor_update", "entity_registry_get",
    "entity_registry_update", "device_registry_get", "device_registry_update",
)
AREA_FIELDS = ("name", "floor_id", "icon", "aliases", "temperature_entity_id", "humidity_entity_id")
FLOOR_FIELDS = ("name", "level", "icon", "aliases")
ENTITY_FIELDS = ("name", "icon", "area_id", "disabled_by", "hidden_by", "entity_category")
DEVICE_FIELDS = ("name_by_user", "area_id", "disabled_by")


class Invalid(ValueError):
    """Invalid or unsupported input."""


class Missing(LookupError):
    """Target does not exist."""


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def string(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise Invalid("Expected a nonempty string of at most 255 characters")
    if any(unicodedata.category(c) == "Cc" for c in value):
        raise Invalid("Control characters are not allowed")
    return value


def project(entry, kind):
    if entry is None:
        return None
    id_key, fields = {
        "area": ("area_id", AREA_FIELDS), "floor": ("floor_id", FLOOR_FIELDS),
        "entity": ("entity_id", ENTITY_FIELDS), "device": ("device_id", DEVICE_FIELDS),
    }[kind]
    id_attr = {"area": "id", "floor": "floor_id", "entity": "entity_id", "device": "id"}[kind]
    out = {id_key: getattr(entry, id_attr)}
    for key in fields:
        value = getattr(entry, key)
        out[key] = value.value if isinstance(value, Enum) else sorted(value) if isinstance(value, set) else value
    return out


def response(status, before=None, requested=None, after=None, *, verified=False, message=None):
    return {"status": status, "before": before, "requested": requested,
            "after": after, "verified": verified, "message": message}


class RegistryBackend:
    """Finite operation dispatch; no request-controlled attributes or commands."""

    def __init__(self, hass):
        self.hass = hass

    def execute(self, operation, data):
        try:
            if operation not in SERVICES or not isinstance(data, dict):
                raise Invalid("Unsupported operation or payload")
            self.areas = ar.async_get(self.hass)
            self.floors = fr.async_get(self.hass)
            self.entities = er.async_get(self.hass)
            self.devices = dr.async_get(self.hass)
            if operation.startswith("area_"):
                return self.area_operation(operation, data)
            if operation.startswith("floor_"):
                return self.floor_operation(operation, data)
            return self.metadata_operation(operation, data)
        except Missing:
            return response("NOT_FOUND", message="Registry target does not exist")
        except (Invalid, ValueError, TypeError):
            return response("VALIDATION_ERROR", message="Invalid, unsupported or conflicting metadata")
        except Exception as exc:
            # Log the technical exception type and operation, never user data,
            # credentials or a raw traceback in the service response.
            _LOGGER.error("Registry operation %s failed (%s); verify state before retry", operation if operation in SERVICES else "rejected", type(exc).__name__)
            return response("WRITE_FAILED", message="Registry operation failed; read back before retry")

    @staticmethod
    def keys(data, allowed, required=()):
        if set(data) - set(allowed) or not set(required) <= set(data):
            raise Invalid("Unexpected or missing fields")

    def named_target(self, kind, data):
        id_key, name_key = ("area_id", "area_name") if kind == "area" else ("floor_id", "floor_name")
        if (id_key in data) == (name_key in data):
            raise Invalid("Supply exactly one target ID or target name")
        registry = self.areas if kind == "area" else self.floors
        if id_key in data:
            target = string(data[id_key])
            entry = registry.async_get_area(target) if kind == "area" else registry.async_get_floor(target)
        else:
            target = normalized(string(data[name_key]))
            entries = registry.async_list_areas() if kind == "area" else registry.async_list_floors()
            found = [x for x in entries if normalized(x.name) == target]
            if len(found) > 1:
                raise Invalid("Ambiguous name; use ID")
            entry = found[0] if found else None
        if entry is None:
            raise Missing()
        return entry

    def validate_area(self, value):
        if value is not None and self.areas.async_get_area(string(value)) is None:
            raise Invalid("Invalid area")

    def validate_floor(self, value):
        if value is not None and self.floors.async_get_floor(string(value)) is None:
            raise Invalid("Invalid floor")

    @staticmethod
    def metadata_values(data):
        for key, value in data.items():
            if key == "aliases":
                if not isinstance(value, list) or len(value) > 100:
                    raise Invalid("Invalid aliases")
                data[key] = {string(x) for x in value}
            elif key == "level":
                if type(value) is not int:
                    raise Invalid("Floor level must be an integer")
            elif key in ("name", "name_by_user"):
                string(value, nullable=key == "name_by_user")
            elif key == "icon":
                if value is not None and not re.fullmatch(r"mdi:[a-z0-9-]+", string(value)):
                    raise Invalid("Invalid icon")

    def collision(self, kind, name, current=None):
        entries = self.areas.async_list_areas() if kind == "area" else self.floors.async_list_floors()
        matches = [e for e in entries if normalized(e.name) == normalized(name) and e != current]
        if len(matches) > 1:
            raise Invalid("Ambiguous duplicate name")
        return matches[0] if matches else None

    @staticmethod
    def changed(before, values):
        return {k: v for k, v in values.items()
                if before[k] != (sorted(v) if isinstance(v, set) else v.value if isinstance(v, Enum) else v)}

    def update(self, kind, entry, values):
        before = project(entry, kind)
        changes = self.changed(before, values)
        requested = {k: sorted(v) if isinstance(v, set) else v.value if isinstance(v, Enum) else v for k, v in values.items()}
        if not changes:
            return response("NO_CHANGE", before, requested, before, verified=True)
        expected = {**before, **requested}
        if kind == "area":
            self.areas.async_update(entry.id, **changes)
            after = project(self.areas.async_get_area(entry.id), kind)
        elif kind == "floor":
            self.floors.async_update(entry.floor_id, **changes)
            after = project(self.floors.async_get_floor(entry.floor_id), kind)
        elif kind == "entity":
            self.entities.async_update_entity(entry.entity_id, **changes)
            after = project(self.entities.async_get(entry.entity_id), kind)
        else:
            self.devices.async_update_device(entry.id, **changes)
            after = project(self.devices.async_get(entry.id), kind)
        verified = after == expected
        return response("SUCCESS" if verified else "VERIFY_FAILED", before, requested, after, verified=verified)

    def area_operation(self, operation, data):
        targets = {"area_id", "area_name"}
        if operation == "area_get":
            self.keys(data, targets)
            after = project(self.named_target("area", data), "area")
            return response("SUCCESS", after=after, verified=True)
        if operation == "area_create":
            self.keys(data, {"name", "floor_id", "icon", "aliases"}, {"name"})
            values = dict(data)
            self.metadata_values(values)
            if "floor_id" in values:
                self.validate_floor(values["floor_id"])
            duplicate = self.collision("area", values["name"])
            if duplicate:
                before = project(duplicate, "area")
                return response("ALREADY_EXISTS", before, after=before, verified=True)
            entry = self.areas.async_create(**values)
            after = project(self.areas.async_get_area(entry.id), "area")
            expected = {"name": values["name"], "floor_id": values.get("floor_id"),
                        "icon": values.get("icon"), "aliases": sorted(values.get("aliases", [])),
                        "temperature_entity_id": None, "humidity_entity_id": None}
            verified = after is not None and all(after[k] == v for k, v in expected.items())
            return response("SUCCESS" if verified else "VERIFY_FAILED", requested=expected, after=after, verified=verified)
        if operation == "area_set_temperature_entity":
            self.keys(data, targets | {"entity_id"}, {"entity_id"})
            entry = self.named_target("area", data)
            entity_id = data["entity_id"]
            if entity_id is not None:
                string(entity_id)
                entity = self.entities.async_get(entity_id)
                state = self.hass.states.get(entity_id)
                if entity is None:
                    raise Missing()
                if (not entity_id.startswith("sensor.") or state is None
                        or state.attributes.get("device_class") != "temperature"
                        or state.attributes.get("unit_of_measurement") not in ("°C", "°F", "K")
                        or entity.entity_category == EntityCategory.DIAGNOSTIC
                        or "device_temperature" in entity_id
                        or any(re.search(r"device[\s_-]+temperature", str(x), re.IGNORECASE)
                               for x in (entity.name, entity.original_name))
                        or er.async_get_effective_area_id(self.hass, entity) != entry.id):
                    raise Invalid("Not an environmental temperature entity in this area")
            return self.update("area", entry, {"temperature_entity_id": entity_id})
        self.keys(data, targets | {"name", "floor_id", "icon", "aliases"})
        entry = self.named_target("area", data)
        values = {k: v for k, v in data.items() if k not in targets}
        self.metadata_values(values)
        if "floor_id" in values:
            self.validate_floor(values["floor_id"])
        if "name" in values and self.collision("area", values["name"], entry):
            raise Invalid("Duplicate name")
        return self.update("area", entry, values)

    def floor_operation(self, operation, data):
        targets = {"floor_id", "floor_name"}
        if operation == "floor_get":
            self.keys(data, targets)
            after = project(self.named_target("floor", data), "floor")
            return response("SUCCESS", after=after, verified=True)
        if operation == "floor_create":
            self.keys(data, set(FLOOR_FIELDS), {"name"})
            values = dict(data)
            # Core allows null level at create, not in async_update's signature.
            if values.get("level", False) is None:
                values.pop("level")
            self.metadata_values(values)
            duplicate = self.collision("floor", values["name"])
            if duplicate:
                before = project(duplicate, "floor")
                return response("ALREADY_EXISTS", before, after=before, verified=True)
            entry = self.floors.async_create(**values)
            after = project(self.floors.async_get_floor(entry.floor_id), "floor")
            expected = {"name": values["name"], "level": values.get("level"),
                        "icon": values.get("icon"), "aliases": sorted(values.get("aliases", []))}
            verified = after is not None and all(after[k] == v for k, v in expected.items())
            return response("SUCCESS" if verified else "VERIFY_FAILED", requested=expected, after=after, verified=verified)
        self.keys(data, targets | set(FLOOR_FIELDS))
        entry = self.named_target("floor", data)
        values = {k: v for k, v in data.items() if k not in targets}
        self.metadata_values(values)
        if "name" in values and self.collision("floor", values["name"], entry):
            raise Invalid("Duplicate name")
        return self.update("floor", entry, values)

    def metadata_operation(self, operation, data):
        entity_kind = operation.startswith("entity_")
        kind = "entity" if entity_kind else "device"
        id_key = "entity_id" if entity_kind else "device_id"
        fields = ENTITY_FIELDS if entity_kind else DEVICE_FIELDS
        is_get = operation.endswith("_get")
        self.keys(data, {id_key} if is_get else {id_key, *fields}, {id_key})
        target = string(data[id_key])
        entry = self.entities.async_get(target) if entity_kind else self.devices.async_get(target)
        if entry is None:
            raise Missing()
        if entity_kind and entry.entity_id != target:
            raise Invalid("Supply the entity_id, not a registry UUID")
        if is_get:
            return response("SUCCESS", after=project(entry, kind), verified=True)
        if not entity_kind and not isinstance(entry, dr.DeviceEntry):
            raise Invalid("Child/composite device update is not supported by this service")
        values = {k: v for k, v in data.items() if k != id_key}
        for k in ("name", "name_by_user"):
            if k in values:
                string(values[k], nullable=True)
        if "icon" in values:
            self.metadata_values({"icon": values["icon"]})
        if "area_id" in values:
            self.validate_area(values["area_id"])
        for k, enum in [("disabled_by", er.RegistryEntryDisabler if entity_kind else dr.DeviceEntryDisabler),
                        ("hidden_by", er.RegistryEntryHider)]:
            if k in values and values[k] is not None:
                # Administrator-owned overrides only, never impersonate an integration.
                if values[k] != "user":
                    raise Invalid("Only user overrides or null are supported")
                values[k] = enum.USER
        if "entity_category" in values and values["entity_category"] is not None:
            values["entity_category"] = EntityCategory(values["entity_category"])
        return self.update(kind, entry, values)


def async_register_registry_services(hass):
    """Register only explicit operations; authenticated admin context is mandatory."""
    if HA_VERSION != "2026.9.4":
        _LOGGER.warning("Registry metadata services disabled: unverified Core version")
        return
    lock = asyncio.Lock()
    backend = RegistryBackend(hass)

    def handler_for(operation):
        async def handle(call):
            user_id = call.context.user_id
            user = await hass.auth.async_get_user(user_id) if user_id else None
            if user is None or not user.is_admin or not user.is_active:
                return response("PERMISSION_DENIED", message="Authenticated active HA administrator required")
            async with lock:
                return backend.execute(operation, dict(call.data))
        return handle

    for operation in SERVICES:
        if not hass.services.has_service(DOMAIN, operation):
            hass.services.async_register(DOMAIN, operation, handler_for(operation),
                                         supports_response=SupportsResponse.OPTIONAL)
