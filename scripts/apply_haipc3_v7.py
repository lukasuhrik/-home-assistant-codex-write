#!/usr/bin/env python3
from pathlib import Path
import runpy, py_compile

ROOT = Path(__file__).resolve().parents[1]

# V7 keeps the reviewed V6 shell-wrapper architecture. The live HAOS
# Bad-file-descriptor failure was traced upstream to Codex 0.160.0 reusing
# one /dev/null descriptor across multiple bwrap --ro-bind-data deny masks.
# Codex 0.162.0+ fixes that by using a distinct descriptor for each mask.
runpy.run_path(str(ROOT / "scripts/apply_haipc3_v6.py"), run_name="__main__")

def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected V6 block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

replace("codex_cli_worker_trusted/config.yaml", 'version: "0.1.73-haipc3"', 'version: "0.1.74-haipc3"')
replace("codex_cli_worker_trusted/verification_mcp.py", '"version": "0.1.73-haipc3"', '"version": "0.1.74-haipc3"')
replace("scripts/validate_candidate.py", "assert config['version'] == '0.1.73-haipc3'", "assert config['version'] == '0.1.74-haipc3'")
replace("codex_cli_worker_trusted/Dockerfile", "ARG CODEX_VERSION=0.160.0", "ARG CODEX_VERSION=0.162.1")

# Add a narrow regression contract: keep the simple V6 shell wrapper and pin
# the fixed upstream Codex version. Do not introduce a native wrapper.
p = ROOT / "codex_cli_worker_trusted/tests/test_managed_isolation.py"
text = p.read_text(encoding="utf-8")
marker = "\n    def test_probe_never_launches_nested_codex"
idx = text.index(marker)
extra = '''

    def test_v7_pins_upstream_fd_fix_and_keeps_shell_wrapper(self):
        docker = (WORKER / "Dockerfile").read_text()
        wrapper = (WORKER / "codex-bwrap").read_text()
        self.assertIn("ARG CODEX_VERSION=0.162.1", docker)
        self.assertTrue(wrapper.startswith("#!/bin/sh"))
        self.assertIn('exec "$REAL_BWRAP"', wrapper)
        self.assertNotIn("codex-bwrap.c", docker)
'''
p.write_text(text[:idx] + extra + text[idx:], encoding="utf-8")
py_compile.compile(str(p), doraise=True)

print("HAIPC3 V7 upstream-Codex-fix transform applied")
