#!/usr/bin/env python3
from pathlib import Path
import runpy, py_compile

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "codex_cli_worker_trusted"

runpy.run_path(str(ROOT / "scripts/apply_haipc3_v8.py"), run_name="__main__")

def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected V8 block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

replace("codex_cli_worker_trusted/config.yaml", 'version: "0.1.75-haipc3"', 'version: "0.1.76-haipc3"')
replace("codex_cli_worker_trusted/verification_mcp.py", '"version": "0.1.75-haipc3"', '"version": "0.1.76-haipc3"')
replace("scripts/validate_candidate.py", "assert config['version'] == '0.1.75-haipc3'", "assert config['version'] == '0.1.76-haipc3'")

# Safe live diagnostics: on isolation failure return only failed boolean check
# names. Never emit file contents, env values, tokens, paths outside the fixed
# reviewed check names, stderr from the sandbox, or raw JSON.
server = W / "server.py"
text = server.read_text(encoding="utf-8")
old = '''        ok = result.returncode == 0 and isinstance(data, dict) and set(data) == expected and all(value is True for value in data.values())
        return {"ok": bool(ok), "checks": {key: value is True for key, value in data.items()} if isinstance(data, dict) and set(data) == expected else {},
                "error": "" if ok else "Command secret isolation gate failed"}'''
new = '''        shape_ok = isinstance(data, dict) and set(data) == expected
        checks = {key: data.get(key) is True for key in sorted(expected)} if shape_ok else {}
        ok = result.returncode == 0 and shape_ok and all(checks.values())
        if ok:
            error = ""
        elif not shape_ok:
            error = "Command secret isolation gate failed: probe shape mismatch"
        else:
            failed = ",".join(key for key, passed in checks.items() if not passed)
            error = "Command secret isolation gate failed: " + failed
        return {"ok": bool(ok), "checks": checks, "error": error}'''
if old not in text:
    raise SystemExit("expected V8 isolation result block not found")
server.write_text(text.replace(old, new, 1), encoding="utf-8")

p = W / "tests/test_managed_isolation.py"
text = p.read_text(encoding="utf-8")
marker = "\n    def test_probe_never_launches_nested_codex"
idx = text.index(marker)
extra = '''

    def test_v9_isolation_failure_reports_only_failed_check_names(self):
        source = (WORKER / "server.py").read_text()
        self.assertIn("probe shape mismatch", source)
        self.assertIn('failed = ",".join', source)
        self.assertNotIn("result.stderr", source[source.index("def sandbox_isolation_probe"):source.index("def sandbox_readiness")])
'''
p.write_text(text[:idx] + extra + text[idx:], encoding="utf-8")

py_compile.compile(str(server), doraise=True)
py_compile.compile(str(p), doraise=True)
print("HAIPC3 V9 safe isolation diagnostics applied")
