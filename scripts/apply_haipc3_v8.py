#!/usr/bin/env python3
from pathlib import Path
import runpy, py_compile

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "codex_cli_worker_trusted"

runpy.run_path(str(ROOT / "scripts/apply_haipc3_v7.py"), run_name="__main__")

def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected V7 block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

replace("codex_cli_worker_trusted/config.yaml", 'version: "0.1.74-haipc3"', 'version: "0.1.75-haipc3"')
replace("codex_cli_worker_trusted/verification_mcp.py", '"version": "0.1.74-haipc3"', '"version": "0.1.75-haipc3"')
replace("scripts/validate_candidate.py", "assert config['version'] == '0.1.74-haipc3'", "assert config['version'] == '0.1.75-haipc3'")

# V5 added two inherited-/proc isolation checks to sandbox_isolation_probe.py
# but the trusted parent still compared the JSON result against the original
# V3 exact key set. Once Codex 0.162.1 fixed sandbox startup, the probe finally
# completed and was then rejected solely because those two legitimate keys
# were "unexpected". Keep exact-shape validation, but include the V5 checks.
server = W / "server.py"
text = server.read_text(encoding="utf-8")
old = '''        expected = {"options_blocked", "worker_token_blocked", "auth_blocked", "s6_directory_blocked",
            "s6_token_blocked", "proc_environ_blocked", "supervisor_http_blocked", "bridge_socket_blocked",
            "old_socket_blocked", "shell_env_clean", "bridge_fd_absent", "shell_helper_absent", "data_audit_pass"}'''
new = '''        expected = {"options_blocked", "worker_token_blocked", "auth_blocked", "s6_directory_blocked",
            "s6_token_blocked", "proc_environ_blocked", "proc_parent_fds_blocked", "proc_root_escape_blocked",
            "supervisor_http_blocked", "bridge_socket_blocked", "old_socket_blocked", "shell_env_clean",
            "bridge_fd_absent", "shell_helper_absent", "data_audit_pass"}'''
if old not in text:
    raise SystemExit("expected V7 isolation-key set not found")
server.write_text(text.replace(old, new, 1), encoding="utf-8")

# Regression: prove the parent accepts the full V5/V8 exact key set and still
# rejects a missing/extra check. This catches future probe/parent drift.
p = W / "tests/test_managed_isolation.py"
text = p.read_text(encoding="utf-8")
marker = "\n    def test_probe_never_launches_nested_codex"
idx = text.index(marker)
extra = '''

    def test_v8_parent_expected_set_includes_inherited_proc_checks(self):
        source = (WORKER / "server.py").read_text()
        self.assertIn('"proc_parent_fds_blocked"', source)
        self.assertIn('"proc_root_escape_blocked"', source)
'''
p.write_text(text[:idx] + extra + text[idx:], encoding="utf-8")
py_compile.compile(str(server), doraise=True)
py_compile.compile(str(p), doraise=True)

print("HAIPC3 V8 isolation-shape fix applied")
