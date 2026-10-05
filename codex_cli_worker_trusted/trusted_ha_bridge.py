"""Finite trusted-parent HA bridge, using the existing private verification IPC."""
import hashlib
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request

OPS = {'ha.ping', 'ha.config', 'ha.services', 'ha.call_service'}
SERVICES = {
    'area_get': {'area_id', 'area_name'},
    'area_update': {'area_id', 'area_name', 'name', 'floor_id', 'icon', 'aliases'},
    'area_set_temperature_entity': {'area_id', 'area_name', 'entity_id'},
    'floor_get': {'floor_id', 'floor_name'},
    'entity_registry_get': {'entity_id'},
    'device_registry_get': {'device_id'},
}
LOG = logging.getLogger('codex.trusted_ha_bridge')
if not LOG.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s HA_BRIDGE %(message)s'))
    LOG.addHandler(handler)
LOG.setLevel(logging.INFO)
LOG.propagate = False


class Invalid(ValueError): pass
class Unavailable(Exception): pass


def strict_pairs(items):
    out = {}
    for key, value in items:
        if key in out: raise Invalid('Duplicate JSON field')
        out[key] = value
    return out


def strict_json(raw):
    def reject_constant(value): raise Invalid('Non-finite JSON')
    return json.loads(raw, object_pairs_hook=strict_pairs, parse_constant=reject_constant)


def validate(request):
    if not isinstance(request, dict): raise Invalid()
    op = request.get('operation')
    if not isinstance(op, str) or op not in OPS: raise Invalid()
    fields = {'operation', 'request_id'} | ({'domain', 'service', 'data'} if op == 'ha.call_service' else set())
    if set(request) != fields: raise Invalid()
    if not isinstance(request['request_id'], str) or not re.fullmatch('[a-f0-9]{32}', request['request_id']): raise Invalid()
    if op != 'ha.call_service': return
    domain, service, data = request['domain'], request['service'], request['data']
    if domain != 'codex_cli' or not isinstance(service, str) or service not in SERVICES: raise Invalid()
    if not isinstance(data, dict) or set(data) - SERVICES[service]: raise Invalid()
    if service.startswith('area_') and service != 'area_create':
        if ('area_id' in data) == ('area_name' in data): raise Invalid()
    if service in ('floor_get', 'floor_update'):
        if ('floor_id' in data) == ('floor_name' in data): raise Invalid()
    required = {'name'} if service in ('area_create', 'floor_create') else {'entity_id'} if (
        service.startswith('entity_registry_') or service == 'area_set_temperature_entity') else {'device_id'} if service.startswith('device_registry_') else set()
    if not required <= set(data): raise Invalid()
    for key, value in data.items():
        if key == 'aliases':
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or not v.strip() or len(v) > 255 for v in value): raise Invalid()
        elif key == 'level':
            if type(value) is not int and not (value is None and service == 'floor_create'): raise Invalid()
        elif value is None:
            nullable = {'icon', 'disabled_by', 'hidden_by', 'entity_category', 'name_by_user'}
            if key == 'name' and service == 'entity_registry_update': continue
            if key == 'entity_id' and service == 'area_set_temperature_entity': continue
            if key == 'area_id' and service in ('entity_registry_update', 'device_registry_update'): continue
            if key == 'floor_id' and service in ('area_create', 'area_update'): continue
            if key not in nullable: raise Invalid()
        elif not isinstance(value, str) or not value.strip() or len(value) > 255: raise Invalid()
        elif key in ('disabled_by', 'hidden_by') and value != 'user': raise Invalid()
        elif key == 'entity_category' and value not in ('config', 'diagnostic'): raise Invalid()


def redact(value, token):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if re.search('token|password|secret|cookie|authorization|private.?key|api.?key', key, re.I)
                else redact(item, token) for key, item in value.items()}
    if isinstance(value, list): return [redact(item, token) for item in value]
    if isinstance(value, str) and token: return value.replace(token, '[REDACTED]')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): raise Unavailable('Redirect denied')


class SupervisorClient:
    """Used exclusively by trusted parent; no credential output or persistence."""
    def available(self): return bool(os.environ.get('SUPERVISOR_TOKEN'))

    def request(self, method, path, data=None):
        token = os.environ.get('SUPERVISOR_TOKEN')
        if not token: raise Unavailable('Parent Supervisor authentication unavailable')
        body = json.dumps(data, allow_nan=False).encode() if data is not None else None
        req = urllib.request.Request('http://supervisor'+path, method=method, data=body,
            headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=15) as response:
                if not 200 <= response.status < 300: raise Unavailable('Non-2xx response')
                raw = response.read(2*1024*1024+1)
                if len(raw) > 2*1024*1024: raise Unavailable('Response limit exceeded')
            return redact(strict_json(raw) if raw else {}, token)
        except Exception:
            # Do not leak headers, body, HTTP exceptions or upstream diagnostics.
            raise Unavailable('Supervisor request failed') from None


class Bridge:
    def __init__(self, verification, client=None):
        self.verification = verification
        self.client = client or SupervisorClient()
        self.lock = threading.Lock()
        self.mutations = {}  # Bounded process-local deduplication, no journal/token file.

    def run(self, task_id, request, capability):
        try: validate(request)
        except (ValueError, TypeError): return {'status':'VALIDATION_ERROR', 'message':'Invalid bridge request'}
        op, request_id = request['operation'], request['request_id']
        with self.lock:
            worker = self.verification.worker
            with worker.lock: turn_id = worker.tasks.get(task_id, {}).get('current_turn_id')
            if not self.verification.active(task_id, turn_id, capability):
                return {'status':'PERMISSION_DENIED', 'request_id':request_id}
            mutation = op == 'ha.call_service'
            digest = hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if mutation and request_id in self.mutations:
                previous = self.mutations[request_id]
                if previous[:3] != (task_id, turn_id, digest):
                    return {'status':'PERMISSION_DENIED', 'request_id':request_id}
                return previous[3]  # No repeated API call, including UNKNOWN outcomes.
            if mutation and len(self.mutations) >= 1024:
                return {'status':'UNAVAILABLE', 'request_id':request_id, 'message':'Bridge mutation ledger full'}
            if not self.client.available():
                return {'status':'UNAVAILABLE', 'request_id':request_id, 'message':'Trusted parent authentication unavailable'}
            unknown = {'status':'UNKNOWN', 'request_id':request_id, 'message':'Outcome unknown; never retry the mutation automatically'}
            if mutation: self.mutations[request_id] = (task_id, turn_id, digest, unknown)
            LOG.info('START operation=%s request_id=%s task_id=%s', op, request_id, task_id)
            try:
                result = self.perform(request)
                result['request_id'] = request_id
                if mutation and not self.verification.active(task_id, turn_id, capability): result = unknown
            except Exception:
                result = unknown if mutation else {'status':'UNAVAILABLE', 'request_id':request_id, 'message':'Trusted API check unavailable'}
            if mutation: self.mutations[request_id] = (task_id, turn_id, digest, result)
            LOG.info('RESULT operation=%s request_id=%s status=%s', op, request_id, result['status'])
            return result

    def perform(self, r):
        validate(r)  # Also defend direct internal use against bypassing the finite allowlist.
        op = r['operation']
        if op == 'ha.ping':
            self.client.request('GET', '/core/api/')
            return {'status':'SUCCESS', 'core_api_reachable':True}
        if op == 'ha.config':
            data = self.client.request('GET', '/core/api/config')
            return {'status':'SUCCESS', 'result':{key:data[key] for key in ('version','state','location_name','time_zone','unit_system','country') if key in data}}
        if op == 'ha.services':
            data = self.client.request('GET', '/core/api/services')
            if not isinstance(data, list): raise Unavailable()
            domains = {row['domain']:sorted(row['services']) for row in data if isinstance(row, dict)
                       and isinstance(row.get('domain'), str) and isinstance(row.get('services'), dict)}
            missing = sorted(set(SERVICES)-set(domains.get('codex_cli', [])))
            return {'status':'SUCCESS', 'services':domains, 'missing_registry_services':missing, 'allowed_registry_services':sorted(SERVICES), 'all_allowed_visible':not missing}
        result = self.client.request('POST', '/core/api/services/codex_cli/'+r['service']+'?return_response', r['data'])
        body = result.get('service_response') if isinstance(result, dict) else None
        statuses = {'SUCCESS','NO_CHANGE','ALREADY_EXISTS','VALIDATION_ERROR','NOT_FOUND','PERMISSION_DENIED','WRITE_FAILED','VERIFY_FAILED'}
        if not isinstance(body, dict) or body.get('status') not in statuses: raise Unavailable()
        safe = {key:body[key] for key in ('status','before','requested','after','verified','message') if key in body}
        return {'status':body['status'], 'result':safe}
