"""Execute the candidate's actual service registration with isolated HA stubs."""
import ast
from pathlib import Path
import sys
import types
import typing
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

SOURCE = Path(__file__).resolve().parents[2] / 'custom_components/codex_cli/__init__.py'

class Unauthorized(Exception):
    def __init__(self, **kwargs):
        self.context = kwargs.get('context')

class StartTaskAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == 'homeassistant.exceptions' and any(a.name == 'Unauthorized' for a in n.names) for n in tree.body))
        register = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_async_register_services')
        names = {n.id for n in ast.walk(register) if isinstance(n, ast.Name)}
        namespace = {n: n for n in names if n.isupper()}
        self.runtime = types.SimpleNamespace(client=types.SimpleNamespace(start_task=AsyncMock(return_value={'ok': True})), coordinator=types.SimpleNamespace(async_request_refresh=AsyncMock()))
        namespace.update(Any=typing.Any, HomeAssistant=object, ServiceCall=object, Unauthorized=Unauthorized, HomeAssistantError=RuntimeError, CodexCliApiError=RuntimeError, _first_runtime=lambda hass: self.runtime)
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), register], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(SOURCE), 'exec'), namespace)
        self.hass = types.SimpleNamespace(services=types.SimpleNamespace(has_service=lambda *args: False, async_register=MagicMock()), auth=types.SimpleNamespace(async_get_user=AsyncMock()))
        core = types.ModuleType('homeassistant.core')
        core.SupportsResponse = types.SimpleNamespace(OPTIONAL='optional')
        with patch.dict(sys.modules, {'homeassistant.core': core}):
            namespace['_async_register_services'](self.hass)
        calls = self.hass.services.async_register.call_args_list
        self.handler = next(c.args[2] for c in calls if c.args[1] == 'SERVICE_START_TASK')
        self.call = types.SimpleNamespace(context=types.SimpleNamespace(user_id='test-user'), data={'ATTR_PROMPT': 'isolated prompt'})

    async def test_non_admin_unauthorized(self):
        self.hass.auth.async_get_user.return_value = types.SimpleNamespace(is_admin=False, is_active=True)
        with self.assertRaises(Unauthorized):
            await self.handler(self.call)
        self.runtime.client.start_task.assert_not_awaited()
        self.runtime.coordinator.async_request_refresh.assert_not_awaited()

    async def test_admin_allowed(self):
        self.hass.auth.async_get_user.return_value = types.SimpleNamespace(is_admin=True, is_active=True)
        self.assertEqual(await self.handler(self.call), {'ok': True})
        self.runtime.client.start_task.assert_awaited_once_with('isolated prompt')
        self.runtime.coordinator.async_request_refresh.assert_awaited_once()

    async def test_inactive_admin_unauthorized(self):
        self.hass.auth.async_get_user.return_value = types.SimpleNamespace(is_admin=True, is_active=False)
        with self.assertRaises(Unauthorized):
            await self.handler(self.call)
        self.runtime.client.start_task.assert_not_awaited()

    async def test_unknown_user_unauthorized(self):
        self.hass.auth.async_get_user.return_value = None
        with self.assertRaises(Unauthorized):
            await self.handler(self.call)
        self.runtime.client.start_task.assert_not_awaited()

    async def test_no_context_user_unauthorized(self):
        self.call.context.user_id = None
        with self.assertRaises(Unauthorized):
            await self.handler(self.call)
        self.hass.auth.async_get_user.assert_not_awaited()
        self.runtime.client.start_task.assert_not_awaited()
