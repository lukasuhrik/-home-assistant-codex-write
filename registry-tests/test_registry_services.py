"""Isolated registry/service mocks. No Core process or production registry writes."""
import asyncio
from dataclasses import dataclass, replace
from enum import StrEnum
import importlib
from pathlib import Path
import sys
import types
import unittest

PRODUCTION = Path(__file__).resolve().parents[1] / 'custom_components/codex_cli'


def mod(name, **values):
    m = types.ModuleType(name)
    m.__path__ = []
    m.__dict__.update(values)
    sys.modules[name] = m
    return m


class Category(StrEnum):
    CONFIG = 'config'
    DIAGNOSTIC = 'diagnostic'


class Disabler(StrEnum):
    USER = 'user'
    INTEGRATION = 'integration'


class Hider(StrEnum):
    USER = 'user'


mod('homeassistant')
mod('homeassistant.const', EntityCategory=Category, __version__='2026.9.4')
mod('homeassistant.core', SupportsResponse=types.SimpleNamespace(OPTIONAL='optional'))
helpers = mod('homeassistant.helpers')
for name, attr in [('area_registry','areas'), ('floor_registry','floors'),
                   ('entity_registry','entities'), ('device_registry','devices')]:
    m = mod('homeassistant.helpers.'+name, async_get=lambda hass, attr=attr: getattr(hass, attr))
    setattr(helpers, name, m)
helpers.entity_registry.RegistryEntryDisabler = Disabler
helpers.entity_registry.RegistryEntryHider = Hider
helpers.device_registry.DeviceEntryDisabler = Disabler
helpers.entity_registry.async_get_effective_area_id = lambda hass, entity: entity.area_id or (
    hass.devices.async_get(entity.device_id).area_id if entity.device_id else None)
pkg = mod('registry_test_pkg')
pkg.__path__ = [str(PRODUCTION)]
mod('registry_test_pkg.const', DOMAIN='codex_cli')
module = importlib.import_module('registry_test_pkg.registry_services')


@dataclass
class Area:
    id: str
    name: str
    floor_id: str | None = None
    icon: str | None = None
    aliases: object = None
    temperature_entity_id: str | None = None
    humidity_entity_id: str | None = None
    def __post_init__(self):
        if self.aliases is None: self.aliases = set()


@dataclass
class Floor:
    floor_id: str
    name: str
    level: int | None = None
    icon: str | None = None
    aliases: object = None
    def __post_init__(self):
        if self.aliases is None: self.aliases = set()


@dataclass
class Entity:
    entity_id: str
    name: str | None = None
    icon: str | None = None
    area_id: str | None = None
    disabled_by: object = None
    hidden_by: object = None
    entity_category: object = None
    original_name: str | None = None
    device_id: str | None = None
    platform: str = 'unchanged'
    unique_id: str = 'unchanged'


@dataclass
class Device:
    id: str
    name_by_user: str | None = None
    area_id: str | None = None
    disabled_by: object = None
    manufacturer: str = 'unchanged'


helpers.device_registry.DeviceEntry = Device


class Registry:
    def __init__(self, cls, id_attr, entries):
        self.cls, self.id_attr = cls, id_attr
        self.entries = {getattr(e,id_attr): e for e in entries}
        self.calls = []
        self.bad = False
        self.fail = False
    def get(self, key): return self.entries.get(key)
    async_get = async_get_area = async_get_floor = get
    def items(self): return list(self.entries.values())
    async_list_areas = async_list_floors = items
    def async_create(self, name, **kwargs):
        self.calls.append(('create',name,kwargs))
        entry = self.cls(**{self.id_attr:'created_'+str(len(self.entries)), 'name':name, **kwargs})
        self.entries[getattr(entry,self.id_attr)] = entry
        return entry
    def update(self, key, **kwargs):
        self.calls.append(('update',key,kwargs))
        if self.fail: raise RuntimeError('mock backend failure')
        if not self.bad: self.entries[key] = replace(self.entries[key], **kwargs)
        return self.entries[key]
    async_update = async_update_entity = async_update_device = update


def fixture():
    hass = types.SimpleNamespace()
    hass.areas = Registry(Area,'id',[Area('kitchen','Kuchyňa',floor_id='lower'),Area('other','Other')])
    hass.floors = Registry(Floor,'floor_id',[Floor('lower','Prízemie'),Floor('upper','Poschodie')])
    hass.devices = Registry(Device,'id',[Device('device',area_id='kitchen')])
    hass.entities = Registry(Entity,'entity_id',[
        Entity('sensor.room_temperature',device_id='device'),
        Entity('sensor.device_temperature',area_id='kitchen'),
        Entity('sensor.diag',area_id='kitchen',entity_category=Category.DIAGNOSTIC),
        Entity('sensor.other',area_id='other'),Entity('switch.bad',area_id='kitchen')])
    states = {k:types.SimpleNamespace(attributes={'device_class':'temperature','unit_of_measurement':'°C'})
              for k in hass.entities.entries}
    hass.state_map = states
    hass.states = types.SimpleNamespace(get=lambda key:states.get(key))
    return hass


class Operations(unittest.TestCase):
    def setUp(self):
        self.hass = fixture()
        self.backend = module.RegistryBackend(self.hass)
    def call(self, op, **data): return self.backend.execute(op,data)
    def status(self, expected, op, **data):
        out=self.call(op,**data);self.assertEqual(out['status'],expected,out);return out
    def test_area_get(self): self.status('SUCCESS','area_get',area_id='kitchen')
    def test_area_get_by_name(self): self.status('SUCCESS','area_get',area_name=' KUCHYŇA ')
    def test_area_create(self): self.status('SUCCESS','area_create',name='WC dole',floor_id='lower')
    def test_area_duplicate(self): self.status('ALREADY_EXISTS','area_create',name=' KUCHYŇA ');self.assertFalse(self.hass.areas.calls)
    def test_area_invalid_floor(self): self.status('VALIDATION_ERROR','area_create',name='new',floor_id='missing');self.assertFalse(self.hass.areas.calls)
    def test_area_update(self): self.status('SUCCESS','area_update',area_id='kitchen',name='Kitchen')
    def test_area_name_target(self): self.status('SUCCESS','area_update',area_name='Kuchyňa',floor_id='upper')
    def test_area_floor_omitted(self):
        self.status('SUCCESS','area_update',area_id='kitchen',name='Kitchen');self.assertEqual(self.hass.areas.get('kitchen').floor_id,'lower')
    def test_area_floor_null(self): self.status('SUCCESS','area_update',area_id='kitchen',floor_id=None);self.assertIsNone(self.hass.areas.get('kitchen').floor_id)
    def test_area_floor_invalid(self): self.status('VALIDATION_ERROR','area_update',area_id='kitchen',floor_id='missing')
    def test_area_unknown(self): self.status('NOT_FOUND','area_update',area_id='missing',name='x')
    def test_area_two_targets(self): self.status('VALIDATION_ERROR','area_update',area_id='kitchen',area_name='Kuchyňa')
    def test_area_duplicate_update(self): self.status('VALIDATION_ERROR','area_update',area_id='kitchen',name='Other')
    def test_area_unsupported(self): self.status('VALIDATION_ERROR','area_update',area_id='kitchen',picture='url')
    def test_area_idempotent(self): self.status('NO_CHANGE','area_update',area_id='kitchen',floor_id='lower');self.assertFalse(self.hass.areas.calls)
    def test_temperature_valid(self): self.status('SUCCESS','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.room_temperature')
    def test_temperature_invalid_domain(self): self.status('VALIDATION_ERROR','area_set_temperature_entity',area_id='kitchen',entity_id='switch.bad')
    def test_temperature_device_temperature(self): self.status('VALIDATION_ERROR','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.device_temperature')
    def test_temperature_diagnostic(self): self.status('VALIDATION_ERROR','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.diag')
    def test_temperature_renamed_internal(self):
        self.hass.entities.entries['sensor.room_temperature'].original_name='Device temperature'
        self.status('VALIDATION_ERROR','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.room_temperature')
    def test_temperature_wrong_area(self): self.status('VALIDATION_ERROR','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.other')
    def test_temperature_missing(self): self.status('NOT_FOUND','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.missing')
    def test_temperature_unit(self):
        self.hass.state_map['sensor.room_temperature'].attributes['unit_of_measurement']='W'
        self.status('VALIDATION_ERROR','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.room_temperature')
    def test_temperature_class(self):
        self.hass.state_map['sensor.room_temperature'].attributes['device_class']='humidity'
        self.status('VALIDATION_ERROR','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.room_temperature')
    def test_temperature_null(self):
        self.status('SUCCESS','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.room_temperature')
        self.status('SUCCESS','area_set_temperature_entity',area_id='kitchen',entity_id=None)
        self.assertIsNotNone(self.hass.entities.get('sensor.room_temperature'))
    def test_temperature_idempotency(self):
        self.call('area_set_temperature_entity',area_id='kitchen',entity_id='sensor.room_temperature')
        self.status('NO_CHANGE','area_set_temperature_entity',area_id='kitchen',entity_id='sensor.room_temperature');self.assertEqual(len(self.hass.areas.calls),1)
    def test_floor_get(self): self.status('SUCCESS','floor_get',floor_id='lower')
    def test_floor_get_by_name(self): self.status('SUCCESS','floor_get',floor_name='Prízemie')
    def test_floor_create(self): self.status('SUCCESS','floor_create',name='Test',level=3,icon='mdi:home')
    def test_floor_duplicate(self): self.status('ALREADY_EXISTS','floor_create',name=' PRÍZEMIE ')
    def test_floor_update(self): self.status('SUCCESS','floor_update',floor_id='lower',level=1)
    def test_floor_name_target(self): self.status('SUCCESS','floor_update',floor_name='Prízemie',name='Lower')
    def test_floor_duplicate_rename(self): self.status('VALIDATION_ERROR','floor_update',floor_id='lower',name='Poschodie')
    def test_floor_missing(self): self.status('NOT_FOUND','floor_update',floor_id='missing',level=1)
    def test_floor_level_null_rejected(self): self.status('VALIDATION_ERROR','floor_update',floor_id='lower',level=None)
    def test_floor_level_bool_rejected(self): self.status('VALIDATION_ERROR','floor_update',floor_id='lower',level=True)
    def test_entity_get(self): self.status('SUCCESS','entity_registry_get',entity_id='sensor.room_temperature')
    def test_entity_name(self): self.status('SUCCESS','entity_registry_update',entity_id='sensor.room_temperature',name='Room')
    def test_entity_icon(self): self.status('SUCCESS','entity_registry_update',entity_id='sensor.room_temperature',icon='mdi:thermometer')
    def test_entity_set_area(self): self.status('SUCCESS','entity_registry_update',entity_id='sensor.room_temperature',area_id='other')
    def test_entity_remove_area(self):
        self.call('entity_registry_update',entity_id='sensor.room_temperature',area_id='other')
        self.status('SUCCESS','entity_registry_update',entity_id='sensor.room_temperature',area_id=None)
    def test_entity_invalid_area(self): self.status('VALIDATION_ERROR','entity_registry_update',entity_id='sensor.room_temperature',area_id='missing')
    def test_entity_missing(self): self.status('NOT_FOUND','entity_registry_update',entity_id='sensor.missing',name='x')
    def test_entity_idempotency(self): self.status('NO_CHANGE','entity_registry_update',entity_id='sensor.room_temperature',name=None);self.assertFalse(self.hass.entities.calls)
    def test_entity_forbidden(self):
        for field in ['platform','config_entry_id','unique_id','capabilities','new_entity_id','device_id','state']:
            with self.subTest(field=field):self.status('VALIDATION_ERROR','entity_registry_update',entity_id='sensor.room_temperature',**{field:'bad'})
        self.assertFalse(self.hass.entities.calls)
    def test_entity_enums(self):
        self.status('SUCCESS','entity_registry_update',entity_id='sensor.room_temperature',disabled_by='user',hidden_by='user',entity_category='config')
        self.status('SUCCESS','entity_registry_update',entity_id='sensor.room_temperature',disabled_by=None,hidden_by=None,entity_category=None)
    def test_entity_integration_reason_rejected(self): self.status('VALIDATION_ERROR','entity_registry_update',entity_id='sensor.room_temperature',disabled_by='integration')
    def test_entity_invalid_category(self): self.status('VALIDATION_ERROR','entity_registry_update',entity_id='sensor.room_temperature',entity_category='arbitrary')
    def test_device_get(self): self.status('SUCCESS','device_registry_get',device_id='device')
    def test_device_name(self): self.status('SUCCESS','device_registry_update',device_id='device',name_by_user='Sensor')
    def test_device_set_area(self): self.status('SUCCESS','device_registry_update',device_id='device',area_id='other')
    def test_device_remove_area(self): self.status('SUCCESS','device_registry_update',device_id='device',area_id=None)
    def test_device_invalid_area(self): self.status('VALIDATION_ERROR','device_registry_update',device_id='device',area_id='missing')
    def test_device_missing(self): self.status('NOT_FOUND','device_registry_update',device_id='missing',name_by_user='x')
    def test_device_idempotency(self): self.status('NO_CHANGE','device_registry_update',device_id='device',area_id='kitchen');self.assertFalse(self.hass.devices.calls)
    def test_device_forbidden(self):
        for field in ['identifiers','connections','manufacturer','model','serial_number','config_entries','via_device_id','new_config_entry_id']:
            with self.subTest(field=field):self.status('VALIDATION_ERROR','device_registry_update',device_id='device',**{field:'bad'})
        self.assertFalse(self.hass.devices.calls)
    def test_device_disable_enable(self):
        self.status('SUCCESS','device_registry_update',device_id='device',disabled_by='user')
        self.status('SUCCESS','device_registry_update',device_id='device',disabled_by=None)
    def test_verification_failure(self):
        self.hass.areas.bad=True;self.status('VERIFY_FAILED','area_update',area_id='kitchen',floor_id='upper')
    def test_backend_failure(self):
        self.hass.areas.fail=True;out=self.status('WRITE_FAILED','area_update',area_id='kitchen',floor_id='upper');self.assertNotIn('mock backend failure',str(out))
    def test_arbitrary_operation(self): self.status('VALIDATION_ERROR','async_delete',area_id='kitchen')
    def test_url_injection(self): self.status('VALIDATION_ERROR','area_get',area_id='kitchen',url='/api/arbitrary')
    def test_unrelated_metadata_preserved(self):
        self.call('entity_registry_update',entity_id='sensor.room_temperature',name='Room')
        e=self.hass.entities.get('sensor.room_temperature');self.assertEqual(e.platform,'unchanged');self.assertEqual(e.unique_id,'unchanged')


class ServiceSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass=fixture();self.handlers={}
        self.hass.services=types.SimpleNamespace(has_service=lambda domain,name:name in self.handlers,
            async_register=lambda domain,name,handler,**kw:self.handlers.setdefault(name,handler))
        self.admin=types.SimpleNamespace(is_admin=True,is_active=True)
        async def get_user(user_id):return self.admin
        self.hass.auth=types.SimpleNamespace(async_get_user=get_user)
        module.async_register_registry_services(self.hass)
    async def call(self,user='admin'):
        return await self.handlers['area_update'](types.SimpleNamespace(context=types.SimpleNamespace(user_id=user),data={'area_id':'kitchen','floor_id':None}))
    async def test_admin_allowed(self): self.assertEqual((await self.call())['status'],'SUCCESS')
    async def test_non_admin_denied(self): self.admin.is_admin=False;self.assertEqual((await self.call())['status'],'PERMISSION_DENIED');self.assertFalse(self.hass.areas.calls)
    async def test_inactive_denied(self): self.admin.is_active=False;self.assertEqual((await self.call())['status'],'PERMISSION_DENIED')
    async def test_no_user_denied(self): self.assertEqual((await self.call(None))['status'],'PERMISSION_DENIED')
    async def test_missing_user_denied(self):
        async def no_user(user_id):return None
        self.hass.auth.async_get_user=no_user;self.assertEqual((await self.call())['status'],'PERMISSION_DENIED')
    async def test_only_allowlisted_services(self): self.assertEqual(set(self.handlers),set(module.SERVICES));self.assertFalse(any('delete' in n for n in self.handlers))
    async def test_repeated_registration(self):
        before=dict(self.handlers);module.async_register_registry_services(self.hass);self.assertEqual(before,self.handlers)
    async def test_other_core_fail_closed(self):
        self.handlers.clear();old=module.HA_VERSION
        try:module.HA_VERSION='2026.9.5';module.async_register_registry_services(self.hass);self.assertFalse(self.handlers)
        finally:module.HA_VERSION=old


if __name__ == '__main__': unittest.main()
