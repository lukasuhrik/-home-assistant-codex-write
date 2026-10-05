"""Trusted-parent/IPC tests; no HA network or production service calls."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'codex_cli_worker_trusted'
sys.path.insert(0,str(ROOT))
import trusted_ha_bridge as bridge
import verification
loader=importlib.machinery.SourceFileLoader('ipc_helper',str(ROOT/'ha-codex-ha'))
spec=importlib.util.spec_from_loader(loader.name,loader)
helper=importlib.util.module_from_spec(spec);loader.exec_module(helper)


class Worker:
    def __init__(self):
        self.lock=threading.RLock()
        self.tasks={'task':{'status':'running','current_turn_id':'turn'}}
        self.cancel=False
    def task_cancellation_requested(self,task):return self.cancel


class Client:
    def __init__(self):self.calls=[];self.response={};self.fail=False;self.token=True
    def available(self):return self.token
    def request(self,*args):
        self.calls.append(args)
        if self.fail:raise bridge.Unavailable('not emitted')
        return self.response


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.worker=Worker();self.engine=verification.Verification(self.worker)
        self.cap='isolated-capability-fixture';self.engine.capabilities['task']=self.cap
        self.client=Client();self.engine.ha_bridge=bridge.Bridge(self.engine,self.client)
        self.logs=io.StringIO()
        self.log_patch=patch.object(bridge.LOG,'handlers',[__import__('logging').StreamHandler(self.logs)])
        self.log_patch.start();self.addCleanup(self.log_patch.stop)
    def request(self,op='ha.ping',**kwargs):return {'operation':op,'request_id':secrets.token_hex(16),**kwargs}
    def dispatch(self,r):return self.engine.dispatch({**r,'capability':self.cap})
    def call(self,domain='codex_cli',service='area_get',data=None):
        return self.request('ha.call_service',domain=domain,service=service,data={'area_id':'kitchen'} if data is None else data)
    def test_ping(self):
        self.assertEqual(self.dispatch(self.request())['status'],'SUCCESS');self.assertEqual(self.client.calls,[('GET','/core/api/')])
    def test_config_projection(self):
        self.client.response={'version':'2026.9.4','secret':'not forwarded'}
        self.assertEqual(self.dispatch(self.request('ha.config'))['result'],{'version':'2026.9.4'})
    def test_services(self):
        self.client.response=[{'domain':'codex_cli','services':{k:{} for k in bridge.SERVICES}}]
        self.assertTrue(self.dispatch(self.request('ha.services'))['all_allowed_visible'])
    def test_missing_services(self):
        self.client.response=[];self.assertEqual(len(self.dispatch(self.request('ha.services'))['missing_registry_services']),6)
    def test_allowed_service(self):
        self.client.response={'service_response':{'status':'NO_CHANGE','verified':True}}
        self.assertEqual(self.dispatch(self.call())['status'],'NO_CHANGE')
        self.assertEqual(self.client.calls[0],('POST','/core/api/services/codex_cli/area_get?return_response',{'area_id':'kitchen'}))
    def test_wrong_domain(self):
        self.assertEqual(self.dispatch(self.call(domain='homeassistant'))['status'],'VALIDATION_ERROR');self.assertFalse(self.client.calls)
    def test_unknown_service(self):self.assertEqual(self.dispatch(self.call(service='delete'))['status'],'VALIDATION_ERROR')
    def test_arbitrary_url(self):self.assertEqual(self.dispatch(self.request(url='http://untrusted'))['status'],'VALIDATION_ERROR')
    def test_method_injection(self):self.assertEqual(self.dispatch(self.request(method='POST'))['status'],'VALIDATION_ERROR')
    def test_arbitrary_path(self):self.assertEqual(self.dispatch(self.request(path='/core/restart'))['status'],'VALIDATION_ERROR')
    def test_unknown_operation(self):self.assertEqual(self.dispatch(self.request('ha.shell'))['status'],'VALIDATION_ERROR')
    def test_forbidden_entity_field(self):self.assertEqual(self.dispatch(self.call(service='entity_registry_update',data={'entity_id':'sensor.x','platform':'bad'}))['status'],'VALIDATION_ERROR')
    def test_forbidden_device_field(self):self.assertEqual(self.dispatch(self.call(service='device_registry_update',data={'device_id':'x','identifiers':[]}))['status'],'VALIDATION_ERROR')
    def test_targets_missing(self):self.assertEqual(self.dispatch(self.call(data={}))['status'],'VALIDATION_ERROR')
    def test_targets_ambiguous(self):self.assertEqual(self.dispatch(self.call(data={'area_id':'x','area_name':'x'}))['status'],'VALIDATION_ERROR')
    def test_bad_id(self):self.assertEqual(self.dispatch({'operation':'ha.ping','request_id':'bad'})['status'],'VALIDATION_ERROR')
    def test_removed_operations_denied(self):
        for service in ("floor_create", "floor_update", "area_create", "entity_registry_update", "device_registry_update"):
            self.assertEqual(self.dispatch(self.call(service=service))["status"], "VALIDATION_ERROR")
        self.assertEqual(self.dispatch(self.request("ha.restart_core"))["status"], "VALIDATION_ERROR")
        self.assertFalse(self.client.calls)
        with self.assertRaises(bridge.Invalid):
            self.engine.ha_bridge.perform(self.request("ha.restart_core"))

    def test_unknown_no_retry(self):
        self.client.fail=True;r=self.call();self.assertEqual(self.dispatch(r)['status'],'UNKNOWN')
        self.assertEqual(self.dispatch(r)['status'],'UNKNOWN');self.assertEqual(len(self.client.calls),1)
    def test_reused_id_different_request(self):
        self.client.response={'service_response':{'status':'SUCCESS'}};r=self.call();self.dispatch(r)
        self.assertEqual(self.dispatch({**self.call(service='area_update',data={'area_id':'kitchen','name':'Changed request'}),'request_id':r['request_id']})['status'],'PERMISSION_DENIED')
    def test_no_parent_token(self):self.client.token=False;self.assertEqual(self.dispatch(self.request())['status'],'UNAVAILABLE');self.assertFalse(self.client.calls)
    def test_expired_capability(self):
        self.engine.capabilities.clear()
        with self.assertRaises(ValueError):self.dispatch(self.request())
    def test_cancelled(self):
        self.worker.cancel=True
        with self.assertRaises(ValueError):self.dispatch(self.request())
    def test_wrong_capability(self):
        with self.assertRaises(ValueError):self.engine.dispatch({**self.request(),'capability':'wrong'})
    def test_read_verification_dispatch_preserved(self):
        with patch.object(self.engine,'run',return_value={'status':'legacy'}) as run:
            self.assertEqual(self.engine.dispatch({'operation':'entity','entity_id':'sensor.x','capability':self.cap}),{'status':'legacy'})
            run.assert_called_once()
    def test_no_auth_header_in_logs(self):
        self.dispatch(self.request());self.assertNotIn('Authorization',self.logs.getvalue());self.assertNotIn(self.cap,self.logs.getvalue())
    def test_mutation_ledger_bounded(self):
        self.engine.ha_bridge.mutations={str(i):None for i in range(1024)}
        self.assertEqual(self.dispatch(self.call())['status'],'UNAVAILABLE');self.assertFalse(self.client.calls)
    def test_concurrent_service_once(self):
        self.client.response={'service_response':{'status':'SUCCESS'}};r=self.call();threads=[threading.Thread(target=self.dispatch,args=(r,)) for _ in range(10)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(self.client.calls),1)
    @unittest.skipUnless(os.environ.get('HA_BRIDGE_REAL_IPC_TEST')=='1', 'Requires runner permitting Unix socket bind; current sandbox denies it')
    def test_unix_socket_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ipc.sock';server=self.engine.serve(path)
            try:
                with patch.object(helper,'SOCKET',str(path)),patch.dict(os.environ,{'HA_VERIFICATION_CAPABILITY':self.cap}):
                    self.assertEqual(helper.send(self.request())['status'],'SUCCESS')
                self.assertEqual(path.stat().st_mode&0o777,0o600)
            finally:server.shutdown();server.server_close()
    @unittest.skipUnless(os.environ.get('HA_BRIDGE_REAL_IPC_TEST')=='1', 'Requires runner permitting Unix socket bind; current sandbox denies it')
    def test_oversized_ipc_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ipc.sock';server=self.engine.serve(path)
            try:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
                    s.connect(str(path));s.sendall(b'x'*16385+b'\n');result=json.loads(s.recv(8192))
                    self.assertEqual(result['status'],'unavailable')
                self.assertFalse(self.client.calls)
            finally:server.shutdown();server.server_close()
    def test_malformed_json_ipc(self):
        with self.assertRaises(ValueError):bridge.strict_json('{')
    def test_duplicate_json_ipc(self):
        with self.assertRaises(ValueError):bridge.strict_json('{"operation":"ha.ping","operation":"ha.restart_core"}')
    def test_nonfinite_json(self):
        with self.assertRaises(ValueError):bridge.strict_json('{"value":NaN}')
    def test_mock_ipc_roundtrip(self):
        engine=self.engine
        class Socket:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def settimeout(self,value):self.timeout=value
            def connect(self,path):self.path=path
            def sendall(self,raw):self.result=engine.dispatch(bridge.strict_json(raw));self.raw=raw
            def recv(self,n):return json.dumps(self.result).encode()+b'\n'
        s=Socket()
        with patch.object(helper.socket,'socket',return_value=s) as constructor,patch.dict(os.environ,{'HA_VERIFICATION_CAPABILITY':self.cap}):
            self.assertEqual(helper.send(self.request())['status'],'SUCCESS')
            constructor.assert_called_once_with(socket.AF_UNIX,socket.SOCK_STREAM)
            self.assertEqual(s.path,'/data/verification.sock');self.assertEqual(s.timeout,40)
            self.assertNotIn(b'Authorization',s.raw)
    def test_helper_request_limit_before_connect(self):
        with patch.dict(os.environ,{'HA_VERIFICATION_CAPABILITY':self.cap}),patch.object(helper.socket,'socket') as sock:
            with self.assertRaises(helper.Invalid):helper.send(self.request('ha.call_service',domain='codex_cli',service='area_create',data={'name':'x'*20000}))
            sock.assert_not_called()
    def test_helper_duplicate_json_rejected(self):
        with self.assertRaises(ValueError):helper.load('{"name":"a","name":"b"}')
    def test_service_url_in_data_rejected(self):
        self.assertEqual(self.dispatch(self.call(data={'area_id':'x','url':'http://bad'}))['status'],'VALIDATION_ERROR')


class ParentCredentials(unittest.TestCase):
    def test_parent_token_available(self):
        with patch.dict(os.environ,{'SUPERVISOR_TOKEN':'isolated-parent-fixture'}):self.assertTrue(bridge.SupervisorClient().available())
    def test_parent_without_token(self):
        with patch.dict(os.environ,{},clear=True):self.assertFalse(bridge.SupervisorClient().available())
    def test_parent_token_absent_from_failure_audit(self):
        fixture='isolated-parent-token-fixture'
        worker=Worker();engine=verification.Verification(worker)
        engine.capabilities['task']='cap-fixture'
        opener=types.SimpleNamespace(open=lambda *a,**kw: (_ for _ in ()).throw(OSError(fixture)))
        request={'operation':'ha.call_service','domain':'codex_cli','service':'area_get','data':{'area_id':'kitchen'},'request_id':secrets.token_hex(16),'capability':'cap-fixture'}
        with patch.dict(os.environ,{'SUPERVISOR_TOKEN':fixture}),patch.object(bridge.urllib.request,'build_opener',return_value=opener),self.assertLogs(bridge.LOG,level='INFO') as logs:
            result=engine.dispatch(request)
        self.assertEqual(result['status'],'UNKNOWN')
        self.assertNotIn(fixture,json.dumps(result));self.assertNotIn(fixture,str(logs.output));self.assertNotIn('Authorization',str(logs.output))
    def test_codex_env_sanitized_even_with_legacy_option(self):
        import server
        with patch.dict(os.environ,{'SUPERVISOR_TOKEN':'isolated-parent-fixture','HASSIO_TOKEN':'isolated-parent-fixture','HA_TOKEN':'fixture'},clear=True),patch.object(server,'read_options',return_value={'HA_TOKEN':'legacy-option-fixture'}):
            result=server.codex_env()
            for name in ('SUPERVISOR_TOKEN','HASSIO_TOKEN','HA_TOKEN','HA_URL'):self.assertNotIn(name,result)
    def test_helper_does_not_read_parent_token(self):
        text=(ROOT/'ha-codex-ha').read_text()
        self.assertNotIn('SUPERVISOR_TOKEN',text);self.assertNotIn('HA_TOKEN',text);self.assertNotIn('urllib',text);self.assertNotIn('Authorization',text)
    def test_http_headers_internal_and_timeout(self):
        response=types.SimpleNamespace(status=200,read=lambda n:b'{"message":"isolated-parent-fixture","password":"fixture"}')
        cm=contextlib.nullcontext(response);seen=[]
        def open_(req,timeout):seen.append((req,timeout));return cm
        opener=types.SimpleNamespace(open=open_)
        with patch.dict(os.environ,{'SUPERVISOR_TOKEN':'isolated-parent-fixture'}),patch.object(bridge.urllib.request,'build_opener',return_value=opener):
            result=bridge.SupervisorClient().request('GET','/core/api/')
            self.assertEqual(seen[0][0].full_url,'http://supervisor/core/api/');self.assertEqual(seen[0][1],15)
            self.assertNotIn('isolated-parent-fixture',json.dumps(result));self.assertEqual(result['password'],'[REDACTED]')
    def test_redirect_denied(self):
        with self.assertRaises(bridge.Unavailable):bridge.NoRedirect().redirect_request(None,None,302,'',{},'http://bad')
    def test_helper_restart_removed(self):
        seen = []
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            helper.main(["restart-core"], lambda request: seen.append(request))
        self.assertFalse(seen)
    def test_helper_no_retry(self):
        seen=[]
        def sender(request):seen.append(request);raise OSError()
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(helper.main(["ping"],sender),1)
        self.assertEqual(len(seen),1)

    def test_helper_missing_capability(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(helper.Invalid):helper.send({'operation':'ha.ping','request_id':'a'*32})


if __name__=='__main__':unittest.main()
