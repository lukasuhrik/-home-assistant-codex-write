"""Validate the release source without reading credentials or HA runtime data."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'codex_cli_worker_trusted'
EXPECTED_PROFILE = '6c8d9cf245a5449dd59ef75a9d15e56d21c0c397470628fe7c55d5933fe33c8a'
EXPECTED_SERVICES = {'area_get', 'floor_get', 'entity_registry_get', 'device_registry_get', 'area_update', 'area_set_temperature_entity'}
assert hashlib.sha256((ADDON / 'apparmor.txt').read_bytes()).hexdigest() == EXPECTED_PROFILE
config = yaml.safe_load((ADDON / 'config.yaml').read_text())
assert config['version'] == '0.1.70-haipc2'
assert config['apparmor'] is True and config['hassio_role'] == 'default'
assert config['homeassistant_api'] is True and config['hassio_api'] is True
assert not any(config.get(key) for key in ('privileged', 'full_access', 'docker_api', 'host_network'))
assert 'HA_TOKEN' not in config['schema'] and 'HA_TOKEN' not in config['options']
assert config['options']['codex_sandbox'] == 'workspace-write'
assert (ROOT / 'custom_components/codex_cli/registry_services.py').is_file()

spec = importlib.util.spec_from_file_location('candidate_bridge_validation', ADDON / 'trusted_ha_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
assert set(bridge.SERVICES) == EXPECTED_SERVICES
assert bridge.OPS == {'ha.ping', 'ha.config', 'ha.services', 'ha.call_service'}
for rel in ('server.py', 'trusted_ha_bridge.py', 'ha-codex-ha'):
    text = (ADDON / rel).read_text()
    assert 'restart_core' not in text and 'restart-core' not in text

patterns = [
    rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    rb'gh[pousr]_[A-Za-z0-9]{30,}',
    rb'github_pat_[A-Za-z0-9_]{40,}',
    rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{30,}',
    rb'AKIA[A-Z0-9]{16}',
    rb'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}',
    rb'Bearer\s+[A-Za-z0-9_+/.=-]{24,}',
]
findings = []
counts = {'python': 0, 'yaml': 0, 'json': 0}
for path in ROOT.rglob('*'):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    if any(part in {'.git', '__pycache__', '.pytest_cache'} for part in rel.parts):
        continue
    if any(part in {'.storage', 'backups', 'logs'} for part in rel.parts) or path.name in {'auth.json', 'options.json', 'secrets.yaml', 'worker_api_token'}:
        findings.append(str(rel))
        continue
    data = path.read_bytes()
    if any(re.search(pattern, data) for pattern in patterns):
        findings.append(str(rel))
    # Literal credential assignments must be explicit synthetic fixtures or empty.
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        continue
    for match in re.finditer(r'''(?i)(?:SUPERVISOR_TOKEN|HA_TOKEN|worker_api_token|password)\s*["']?\s*[:=]\s*["']([^"'\n]+)["']''', text):
        value = match.group(1)
        if len(value) >= 20 and not any(word in value.lower() for word in ('fixture', 'example', 'test', 'mock', 'isolated', 'redacted', '${{')):
            findings.append(str(rel))
    if path.suffix == '.py' or path.name == 'ha-codex-ha':
        compile(data, str(path), 'exec')
        counts['python'] += 1
    elif path.suffix in {'.yaml', '.yml'}:
        yaml.safe_load(text)
        counts['yaml'] += 1
    elif path.suffix == '.json':
        json.loads(text)
        counts['json'] += 1
if findings:
    print('SECRET SCAN FAIL')
    sys.exit(1)
print('SECRET SCAN PASS')
print('CANDIDATE METADATA/SYNTAX/BRIDGE IMPORT PASS', counts)
