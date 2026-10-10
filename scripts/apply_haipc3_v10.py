#!/usr/bin/env python3
from pathlib import Path
import runpy, py_compile

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "codex_cli_worker_trusted"

runpy.run_path(str(ROOT / "scripts/apply_haipc3_v9.py"), run_name="__main__")

def repl(path, old, new):
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"missing block in {path}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")

repl("codex_cli_worker_trusted/config.yaml", 'version: "0.1.76-haipc3"', 'version: "0.1.77-haipc3"')
repl("codex_cli_worker_trusted/verification_mcp.py", '"version": "0.1.76-haipc3"', '"version": "0.1.77-haipc3"')
repl("scripts/validate_candidate.py", "assert config['version'] == '0.1.76-haipc3'", "assert config['version'] == '0.1.77-haipc3'")

(W / "requirements.toml").write_text(
'''allowed_sandbox_modes = ["read-only", "workspace-write"]

[permissions.filesystem]
deny_read = [
  "/run/s6",
  "/run/codex-bridge",
  "/proc",
  "/data"
]
''', encoding="utf-8")

probe = W / "sandbox_isolation_probe.py"
s = probe.read_text(encoding="utf-8")
s = s.replace('''def data_audit():
    if not directory_readable("/data"):
        return False
''','''def data_audit():
    if not directory_readable("/data"):
        return True
''',1)
probe.write_text(s, encoding="utf-8")

server = W / "server.py"
s = server.read_text(encoding="utf-8")
old = '''        expected = {"/run/s6", "/run/codex-bridge", "/data/options.json", "/data/worker_api_token",
            "/data/verification.sock", "/data/codex-home/auth.json", "/data/codex-home/sessions",
            "/data/codex-home/history.jsonl"}'''
new = '''        expected = {"/run/s6", "/run/codex-bridge", "/proc", "/data"}'''
if old not in s:
    raise SystemExit("requirements_status block missing")
server.write_text(s.replace(old,new,1), encoding="utf-8")

for rel in ("codex_cli_worker_trusted/sandbox_isolation_probe.py","codex_cli_worker_trusted/server.py"):
    py_compile.compile(str(ROOT / rel), doraise=True)

print("HAIPC3 V10 isolation policy applied")
