#!/usr/bin/env python3
from pathlib import Path
import runpy
import py_compile

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "codex_cli_worker_trusted"

# Start from the exact CI-passed V4 transform.
runpy.run_path(str(ROOT / "scripts/apply_haipc3_v4.py"), run_name="__main__")

def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected V4 block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

# New immutable version.
replace("codex_cli_worker_trusted/config.yaml", 'version: "0.1.71-haipc3"', 'version: "0.1.72-haipc3"')
replace("codex_cli_worker_trusted/verification_mcp.py", '"version": "0.1.71-haipc3"', '"version": "0.1.72-haipc3"')
replace("scripts/validate_candidate.py", "assert config['version'] == '0.1.71-haipc3'", "assert config['version'] == '0.1.72-haipc3'")

# HAOS rejects a nested fresh /proc mount. Codex 0.160.0 then falls back to
# fresh user+PID namespaces with the inherited container procfs. Permit that
# fallback only while retaining network isolation, cap drop and secret env
# removal. The end-to-end isolation probe below is the runtime authorization
# gate and must prove that inherited procfs cannot expose parent secrets/FDS.
guard = W / "command_guard.py"
guard.write_text('''"""Require fresh user/PID/network namespaces; never expose trusted secrets to commands."""
import os
import sys

REAL_BWRAP = "/opt/codex-sandbox/bwrap.real"


def guarded_args(args):
    if args == ["--help"]:
        return ["--cap-drop", "ALL", "--help"]
    if "--" not in args:
        raise ValueError("Missing command separator")
    split = args.index("--")
    options, command = args[:split], args[split + 1:]
    if not command:
        raise ValueError("Command required")
    if "--unshare-user" not in options:
        raise ValueError("Fresh user namespace required")
    if "--unshare-pid" not in options:
        raise ValueError("Fresh PID namespace required")
    # --proc /proc is preferred, but HAOS/AppArmor may deny the nested mount.
    # In that documented Codex fallback, readiness is granted only after
    # sandbox_isolation_probe.py proves inherited procfs cannot expose parent
    # secrets or file descriptors.
    extra = ["--unshare-net", "--cap-drop", "ALL"]
    for name in ("HA_VERIFICATION_CAPABILITY", "SUPERVISOR_TOKEN", "HASSIO_TOKEN", "HA_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        extra.extend(["--unsetenv", name])
    return [*options, *extra, "--", *command]


if __name__ == "__main__":
    try:
        args = guarded_args(sys.argv[1:])
        os.execv(REAL_BWRAP, [REAL_BWRAP, *args])
    except (OSError, ValueError):
        print("Command isolation unavailable; refusing execution", file=sys.stderr)
        sys.exit(1)
''', encoding="utf-8")

# Strengthen the inherited-/proc live gate: parent proc roots and FD directories
# must not offer a path around deny_read. No file contents are emitted.
probe = W / "sandbox_isolation_probe.py"
text = probe.read_text(encoding="utf-8")
anchor = '    result["bridge_socket_blocked"] = not socket_accessible("/run/codex-bridge/bridge.sock")\n'
insert = '''    # When HAOS denies a fresh /proc mount, Codex may inherit the container
    # procfs. Parent process roots and descriptor tables must therefore remain
    # inaccessible; otherwise /proc could bypass ordinary deny_read paths.
    proc_parent_fds_blocked = True
    proc_root_escape_blocked = True
    for proc_dir in glob.glob("/proc/[0-9]*"):
        try:
            if os.path.samefile(proc_dir, "/proc/self"):
                continue
        except OSError:
            continue
        if directory_readable(proc_dir + "/fd"):
            proc_parent_fds_blocked = False
        for rel in (
            "/data/options.json",
            "/data/worker_api_token",
            "/data/codex-home/auth.json",
            "/run/s6/container_environment/SUPERVISOR_TOKEN",
        ):
            if readable(proc_dir + "/root" + rel):
                proc_root_escape_blocked = False
    result["proc_parent_fds_blocked"] = proc_parent_fds_blocked
    result["proc_root_escape_blocked"] = proc_root_escape_blocked
'''
if anchor not in text:
    raise SystemExit("isolation probe anchor not found")
probe.write_text(text.replace(anchor, insert + anchor, 1), encoding="utf-8")

# Update V4 readiness documentation so the implemented security model is explicit.
server = W / "server.py"
text = server.read_text(encoding="utf-8")
old = '''    succeeds. Normal commands still pass through command_guard.py, which keeps
    the fresh PID + fresh /proc requirement unchanged.
'''
new = '''    succeeds. Normal commands still pass through command_guard.py, which requires
    fresh user/PID namespaces, network isolation, cap drop and secret-env removal.
    If HAOS denied a fresh /proc mount, the isolation probe must additionally prove
    that inherited procfs cannot expose parent process secrets or descriptors.
'''
if old not in text:
    raise SystemExit("V4 readiness documentation block not found")
server.write_text(text.replace(old, new, 1), encoding="utf-8")

# Update guard regression tests to cover the HAOS no-proc fallback.
p = W / "tests/test_managed_isolation.py"
text = p.read_text(encoding="utf-8")
start = text.index("    def test_guard_requires_fresh_proc_and_drops_capability")
end = text.index("\n    def test_probe_never_launches_nested_codex", start)
block = '''    def test_guard_accepts_verified_no_proc_fallback_and_drops_capability(self):
        for args in (
            ["--ro-bind", "/", "/", "--unshare-user", "--unshare-pid", "--proc", "/proc", "--", "/bin/true"],
            ["--ro-bind", "/", "/", "--unshare-user", "--unshare-pid", "--", "/bin/true"],
        ):
            hardened = guard.guarded_args(args)
            self.assertIn("--unshare-net", hardened)
            self.assertIn(["--unsetenv", "HA_VERIFICATION_CAPABILITY"], [hardened[i:i+2] for i in range(len(hardened))])
            self.assertNotIn("--bind", hardened)
            self.assertEqual(hardened[-2:], ["--", "/bin/true"])
        with self.assertRaises(ValueError):
            guard.guarded_args(["--unshare-pid", "--", "/bin/true"])
        with self.assertRaises(ValueError):
            guard.guarded_args(["--unshare-user", "--", "/bin/true"])
'''
p.write_text(text[:start] + block + text[end:], encoding="utf-8")

# V4's bwrap test expected no-proc to be refused by command_guard. In V5 it
# must reach the guarded real-bwrap path while missing PID/user still refuses.
p = W / "tests/test_bwrap.py"
text = p.read_text(encoding="utf-8")
start = text.index("    def test_probe_lookalikes_missing_required_isolation_are_refused")
end = text.index("\n    def test_version_and_help_remain_usable", start)
block = '''    def test_probe_missing_pid_is_refused_but_no_proc_fallback_uses_guarded_path(self) -> None:
        missing_pid = [arg for arg in PROBE if arg != "--unshare-pid"]
        if self.record.exists():
            self.record.unlink()
        result = subprocess.run(
            [str(self.wrapper), *missing_pid],
            env={**os.environ, "TEST_RECORD": str(self.record),
                 "TEST_STDERR": PROC_ERROR.hex(), "TEST_EXIT": "19"},
            capture_output=True, timeout=5, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Command isolation unavailable", result.stderr)
        self.assertFalse(self.record.exists())

        proc_index = PROBE.index("--proc")
        no_proc = PROBE[:proc_index] + PROBE[proc_index + 2:]
        result, record = self.run_wrapper(no_proc, exit_code=19)
        self.assertEqual(result.returncode, 19)
        self.assertEqual(result.stderr, PROC_ERROR)
        self.assertIn("--unshare-net", record["args"])
        self.assertIn(["--unsetenv", "HA_VERIFICATION_CAPABILITY"], [record["args"][i:i+2] for i in range(len(record["args"]))])
'''
p.write_text(text[:start] + block + text[end:], encoding="utf-8")

# Add a focused unit test for the new parent-proc checks without reading secrets.
p = W / "tests/test_managed_isolation.py"
text = p.read_text(encoding="utf-8")
marker = "\n    def test_probe_never_launches_nested_codex"
idx = text.index(marker)
extra = '''
    def test_isolation_probe_source_checks_parent_proc_escape_paths(self):
        source = (WORKER / "sandbox_isolation_probe.py").read_text()
        self.assertIn("proc_parent_fds_blocked", source)
        self.assertIn("proc_root_escape_blocked", source)
        self.assertIn("/root/data/options.json", source.replace(' + rel', ''))
'''
# Keep the test simple and source-based; the full probe executes only on HAOS live.
text = text[:idx] + extra + text[idx:]
# Remove the fragile third assertion and replace with stable path literals.
text = text.replace('        self.assertIn("/root/data/options.json", source.replace(\' + rel\', \'\'))\n',
                    '        self.assertIn("/data/options.json", source)\n        self.assertIn("/run/s6/container_environment/SUPERVISOR_TOKEN", source)\n')
p.write_text(text, encoding="utf-8")

for rel in (
    "codex_cli_worker_trusted/command_guard.py",
    "codex_cli_worker_trusted/sandbox_isolation_probe.py",
    "codex_cli_worker_trusted/server.py",
    "codex_cli_worker_trusted/tests/test_managed_isolation.py",
    "codex_cli_worker_trusted/tests/test_bwrap.py",
):
    py_compile.compile(str(ROOT / rel), doraise=True)

print("HAIPC3 V5 transform applied")
