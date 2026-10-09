#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "codex_cli_worker_trusted"

def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected source block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

# Only normalize Codex's harmless internal /proc compatibility probe.
replace(
    "codex_cli_worker_trusted/codex-bwrap",
    """  # Codex's internal probe ends in exactly `-- /[usr/]bin/true`.
  # Real commands re-enter the Codex sandbox helper instead. Stop inspecting
  # arguments at the first separator so task payloads can never match.
  as_pid_1=0 user_ns=0 pid_ns=0 ipc_ns=0 proc_mount=0
  new_session=0 die_with_parent=0
""",
    """  # Codex's internal compatibility probe ends in exactly `-- /[usr/]bin/true`.
  # Require the stable security-significant shape: fresh user/PID namespaces
  # plus an attempted fresh /proc. Extra diagnostic flags may vary between
  # CLI/container builds. Real task payloads cannot match because the command
  # after `--` must be exactly true.
  user_ns=0 pid_ns=0 proc_mount=0
""",
)
for line in (
    "      --as-pid-1) as_pid_1=1 ;;\n",
    "      --new-session) new_session=1 ;;\n",
    "      --die-with-parent) die_with_parent=1 ;;\n",
    "      --unshare-ipc) ipc_ns=1 ;;\n",
):
    replace("codex_cli_worker_trusted/codex-bwrap", line, "")
replace(
    "codex_cli_worker_trusted/codex-bwrap",
    '            [ "$as_pid_1$user_ns$pid_ns$ipc_ns$proc_mount$new_session$die_with_parent" = 1111111 ]\n',
    '            [ "$user_ns$pid_ns$proc_mount" = 111 ]\n',
)

replace(
    "codex_cli_worker_trusted/config.yaml",
    'version: "0.1.70-haipc3"',
    'version: "0.1.71-haipc3"',
)

old_readiness = '''def sandbox_readiness() -> dict[str, Any]:
    """No task may run without managed constraints AND actual isolation."""
    mode = str(read_options().get("codex_sandbox") or DEFAULT_OPTIONS["codex_sandbox"])
    binary = shutil.which("bwrap") or ""
    policy = requirements_status()
    unsupported = mode not in {"read-only", "workspace-write"}
    namespace_probe = {"ok": False, "error": "Unsupported sandbox mode" if unsupported else "Managed policy unavailable"}
    proc_probe = {"ok": False, "error": "Fresh proc probe not run"}
    isolation = {"ok": False, "error": "Isolation probe not run"}
    if not unsupported and policy["ok"] and binary:
        namespace_probe = _bubblewrap_probe(binary, mount_proc=True, codex_mode=mode)
        if namespace_probe.get("namespace_ok", namespace_probe["ok"]):
            proc_probe = _bubblewrap_probe(binary, mount_proc=True)
        if namespace_probe["ok"] and proc_probe["ok"]:
            isolation = sandbox_isolation_probe()
    ready = bool(not unsupported and policy["ok"] and namespace_probe["ok"] and proc_probe["ok"] and isolation["ok"])
    errors = [item.get("error") for item in (policy, namespace_probe, proc_probe, isolation) if not item["ok"]]
    return {"mode": mode, "required": True, "ready": ready, "bubblewrap_ready": ready,
        "proc_mount_supported": bool(proc_probe["ok"]), "bubblewrap_binary": binary,
        "namespace_probe": namespace_probe, "proc_probe": proc_probe, "isolation_probe": isolation,
        "managed_policy": policy, "message": "Managed command isolation passed" if ready else "; ".join(filter(None, errors)),
        "checked_at": utc_now()}
'''
new_readiness = '''def sandbox_readiness() -> dict[str, Any]:
    """Require managed policy plus the real Codex sandbox and isolation gate.

    HAOS/AppArmor may reject a standalone nested /proc mount. That raw probe is
    retained as telemetry only. Readiness is granted only when the real pinned
    Codex sandbox command succeeds and the end-to-end secret/isolation probe
    succeeds. Normal commands still pass through command_guard.py, which keeps
    the fresh PID + fresh /proc requirement unchanged.
    """
    mode = str(read_options().get("codex_sandbox") or DEFAULT_OPTIONS["codex_sandbox"])
    binary = shutil.which("bwrap") or ""
    policy = requirements_status()
    unsupported = mode not in {"read-only", "workspace-write"}
    namespace_probe = {"ok": False, "error": "Unsupported sandbox mode" if unsupported else "Managed policy unavailable"}
    proc_probe = {"ok": False, "error": "Fresh proc probe not run"}
    isolation = {"ok": False, "error": "Isolation probe not run"}
    if not unsupported and policy["ok"] and binary:
        raw_namespace = _bubblewrap_probe(binary, mount_proc=False)
        namespace_probe = raw_namespace
        if raw_namespace["ok"]:
            codex_probe = _codex_sandbox_probe(mode)
            namespace_probe = {
                "ok": bool(codex_probe["ok"]),
                "error": codex_probe["error"],
                "namespace_ok": True,
                "codex_probe": codex_probe,
            }
            if codex_probe["ok"]:
                proc_probe = _bubblewrap_probe(binary, mount_proc=True)
                isolation = sandbox_isolation_probe()
    ready = bool(not unsupported and policy["ok"] and namespace_probe["ok"] and isolation["ok"])
    errors = [item.get("error") for item in (policy, namespace_probe, isolation) if not item["ok"]]
    return {"mode": mode, "required": True, "ready": ready, "bubblewrap_ready": ready,
        "proc_mount_supported": bool(proc_probe["ok"]), "bubblewrap_binary": binary,
        "namespace_probe": namespace_probe, "proc_probe": proc_probe, "isolation_probe": isolation,
        "managed_policy": policy, "message": "Managed command isolation passed" if ready else "; ".join(filter(None, errors)),
        "checked_at": utc_now()}
'''
replace("codex_cli_worker_trusted/server.py", old_readiness, new_readiness)

p = W / "tests/test_managed_isolation.py"
text = p.read_text(encoding="utf-8")
anchor = '             patch.object(server, "_bubblewrap_probe", return_value={"ok":True,"error":""}), \\\n'
needle = '             patch.object(server, "sandbox_isolation_probe", return_value={"ok":False,"error":"isolation failed"}):'
if anchor + needle not in text:
    raise SystemExit("expected managed isolation anchors not found")
text = text.replace(
    anchor + needle,
    anchor + '             patch.object(server, "_codex_sandbox_probe", return_value={"ok":True,"error":""}), \\\n' + needle,
    1,
)
p.write_text(text, encoding="utf-8")

p = W / "tests/test_sandbox_readiness.py"
text = p.read_text(encoding="utf-8")
start = text.index("    def readiness(")
end = text.index('\n\nif __name__ == "__main__":', start)
block = '''    def readiness(self, raw_results: list[subprocess.CompletedProcess], codex: subprocess.CompletedProcess | None = None,
                  isolation_ok: bool = True, mode: str = "workspace-write") -> dict:
        """Return readiness with explicit raw namespace/proc, Codex and isolation outcomes."""
        codex = codex or completed()
        with (
            patch.object(server, "read_options", return_value={"codex_sandbox": mode}),
            patch.object(server.shutil, "which", return_value="/opt/codex-sandbox/bwrap"),
            patch.object(server, "codex_binary_path", return_value=server.CODEX_BINARY),
            patch.object(server, "codex_env", return_value={}),
            patch.object(server, "requirements_status", return_value={"ok":True,"error":""}),
            patch.object(server, "sandbox_isolation_probe", return_value={"ok":isolation_ok,"error":"" if isolation_ok else "isolation failed"}),
            patch.object(server.subprocess, "run", side_effect=[raw_results[0], codex, *raw_results[1:]]) as run,
        ):
            result = server.sandbox_readiness()
        self.assertEqual(run.call_count, len(raw_results) + 1)
        return result

    def test_proc_denial_is_telemetry_when_real_codex_and_isolation_pass(self) -> None:
        result = self.readiness([completed(), completed(1, "proc denied")])
        self.assertTrue(result["ready"])
        self.assertTrue(result["namespace_probe"]["codex_probe"]["ok"])
        self.assertFalse(result["proc_mount_supported"])
        self.assertEqual(result["message"], "Managed command isolation passed")

    def test_broken_real_codex_path_blocks_readiness(self) -> None:
        result = self.readiness([completed()], codex=completed(1, "Codex failed"))
        self.assertFalse(result["ready"])
        self.assertFalse(result["namespace_probe"]["codex_probe"]["ok"])
        self.assertIn("Codex failed", result["message"])

    def test_isolation_failure_blocks_successful_codex_path(self) -> None:
        result = self.readiness([completed(), completed(1, "proc denied")], isolation_ok=False)
        self.assertFalse(result["ready"])
        self.assertIn("isolation failed", result["message"])

    def test_fresh_proc_success_is_reported(self) -> None:
        result = self.readiness([completed(), completed()], mode="read-only")
        self.assertTrue(result["ready"])
        self.assertTrue(result["proc_mount_supported"])

    def test_namespace_failure_skips_real_codex_and_isolation(self) -> None:
        with (
            patch.object(server, "read_options", return_value={"codex_sandbox": "workspace-write"}),
            patch.object(server.shutil, "which", return_value="/opt/codex-sandbox/bwrap"),
            patch.object(server, "requirements_status", return_value={"ok":True,"error":""}),
            patch.object(server, "_bubblewrap_probe", return_value={"ok":False,"error":"namespace denied"}) as raw,
            patch.object(server, "_codex_sandbox_probe") as codex_probe,
            patch.object(server, "sandbox_isolation_probe") as isolation,
        ):
            result = server.sandbox_readiness()
        self.assertFalse(result["ready"])
        raw.assert_called_once()
        codex_probe.assert_not_called()
        isolation.assert_not_called()

    def test_danger_mode_does_not_run_any_probe(self) -> None:
        with (
            patch.object(server, "read_options", return_value={"codex_sandbox": "danger-full-access"}),
            patch.object(server, "requirements_status", return_value={"ok":True,"error":""}),
            patch.object(server, "_bubblewrap_probe") as raw,
            patch.object(server, "_codex_sandbox_probe") as codex_probe,
        ):
            result = server.sandbox_readiness()
        self.assertFalse(result["ready"])
        self.assertTrue(result["required"])
        raw.assert_not_called()
        codex_probe.assert_not_called()
'''
p.write_text(text[:start] + block + text[end:], encoding="utf-8")

p = W / "tests/test_server.py"
text = p.read_text(encoding="utf-8")
start = text.index("    def test_workspace_sandbox_rejects_no_proc_fallback")
end = text.index("\n    def test_danger_full_access_is_rejected", start)
block = '''    def test_workspace_sandbox_accepts_proc_telemetry_failure_only_after_real_gates_pass(self) -> None:
        with (
            patch.object(server, "read_options", return_value={"codex_sandbox": "workspace-write"}),
            patch.object(server.shutil, "which", return_value="/usr/bin/bwrap"),
            patch.object(server, "requirements_status", return_value={"ok":True,"error":""}),
            patch.object(server, "_codex_sandbox_probe", return_value={"ok":True,"error":""}),
            patch.object(server, "sandbox_isolation_probe", return_value={"ok":True,"error":""}),
            patch.object(
                server,
                "_bubblewrap_probe",
                side_effect=[
                    {"ok": True, "error": ""},
                    {"ok": False, "error": "proc mount denied"},
                ],
            ) as probe,
        ):
            result = server.sandbox_readiness()

        self.assertTrue(result["required"])
        self.assertTrue(result["ready"])
        self.assertTrue(result["bubblewrap_ready"])
        self.assertFalse(result["proc_mount_supported"])
        self.assertEqual(probe.call_count, 2)
'''
p.write_text(text[:start] + block + text[end:], encoding="utf-8")

p = W / "tests/test_bwrap.py"
text = p.read_text(encoding="utf-8")
start = text.index("    def test_real_commands_and_lookalikes_bypass_helper")
end = text.index("\n    def test_version_and_help_remain_usable", start)
block = '''    def test_real_commands_and_lookalikes_bypass_helper(self) -> None:
        """Real commands and non-isolation probe lookalikes stay on the normal guarded path."""
        separator = PROBE.index("--")
        cases = [
            [*PROBE[:separator + 1], "/usr/local/bin/codex", "--apply-seccomp-then-exec", "--", "/bin/true"],
            [*PROBE, "extra"], [*PROBE[:-1], "true"],
            [*PROBE[:separator + 1], "/bin/sh", "-c", " ".join(PROBE)],
        ]
        for flag in ("--as-pid-1", "--new-session", "--die-with-parent", "--unshare-ipc"):
            cases.append([arg for arg in PROBE if arg != flag])
        for args in cases:
            with self.subTest(args=args):
                result, record = self.run_wrapper(args, exit_code=19)
                self.assertEqual(result.stderr, PROC_ERROR)
                self.assertEqual(result.returncode, 19)
                self.assertEqual(record["args"][:args.index("--")], args[:args.index("--")])
                self.assertIn(["--unsetenv", "HA_VERIFICATION_CAPABILITY"], [record["args"][i:i+2] for i in range(len(record["args"]))])

    def test_probe_lookalikes_missing_required_isolation_are_refused(self) -> None:
        """Missing PID namespace or fresh /proc must never be normalized into a successful-looking probe."""
        proc_index = PROBE.index("--proc")
        cases = [
            [arg for arg in PROBE if arg != "--unshare-pid"],
            PROBE[:proc_index] + PROBE[proc_index + 2:],
        ]
        for args in cases:
            with self.subTest(args=args):
                result, _ = self.run_wrapper(args, exit_code=19)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"Command isolation unavailable", result.stderr)
'''
p.write_text(text[:start] + block + text[end:], encoding="utf-8")

# Ensure Python syntax before CI.
import py_compile
for rel in (
    "codex_cli_worker_trusted/server.py",
    "codex_cli_worker_trusted/tests/test_managed_isolation.py",
    "codex_cli_worker_trusted/tests/test_sandbox_readiness.py",
    "codex_cli_worker_trusted/tests/test_server.py",
):
    py_compile.compile(str(ROOT / rel), doraise=True)

print("HAIPC3 V4 transform applied")

# Refine probe regression expectations after the base transform.
p = W / "tests/test_bwrap.py"
text = p.read_text(encoding="utf-8")
start = text.index("    def test_real_commands_and_lookalikes_bypass_helper")
end = text.index("\n    def test_version_and_help_remain_usable", start)
block = '''    def test_real_commands_and_lookalikes_bypass_helper(self) -> None:
        """Real commands and true probe near-matches stay on the normal guarded path."""
        separator = PROBE.index("--")
        cases = [
            [*PROBE[:separator + 1], "/usr/local/bin/codex", "--apply-seccomp-then-exec", "--", "/bin/true"],
            [*PROBE, "extra"], [*PROBE[:-1], "true"],
            [*PROBE[:separator + 1], "/bin/sh", "-c", " ".join(PROBE)],
            [arg for arg in PROBE if arg != "--unshare-user"],
        ]
        for args in cases:
            with self.subTest(args=args):
                result, record = self.run_wrapper(args, exit_code=19)
                self.assertEqual(result.stderr, PROC_ERROR)
                self.assertEqual(result.returncode, 19)
                self.assertEqual(record["args"][:args.index("--")], args[:args.index("--")])
                self.assertIn(["--unsetenv", "HA_VERIFICATION_CAPABILITY"], [record["args"][i:i+2] for i in range(len(record["args"]))])

    def test_probe_variants_with_stable_isolation_shape_are_normalized(self) -> None:
        """Nonessential diagnostic flags may vary; stable user/PID/proc shape is sufficient."""
        for flag in ("--as-pid-1", "--new-session", "--die-with-parent", "--unshare-ipc"):
            args = [arg for arg in PROBE if arg != flag]
            with self.subTest(flag=flag):
                result, record = self.run_wrapper(args, exit_code=17)
                self.assertEqual(result.returncode, 17)
                self.assertEqual(result.stderr, RECOGNIZED_ERROR)
                self.assertEqual(record["args"], ["--cap-drop", "ALL", *args])

    def test_probe_lookalikes_missing_required_isolation_are_refused(self) -> None:
        """Missing PID namespace or fresh /proc must never be normalized."""
        proc_index = PROBE.index("--proc")
        cases = [
            [arg for arg in PROBE if arg != "--unshare-pid"],
            PROBE[:proc_index] + PROBE[proc_index + 2:],
        ]
        for args in cases:
            with self.subTest(args=args):
                if self.record.exists():
                    self.record.unlink()
                result = subprocess.run(
                    [str(self.wrapper), *args],
                    env={**os.environ, "TEST_RECORD": str(self.record),
                         "TEST_STDERR": PROC_ERROR.hex(), "TEST_EXIT": "19"},
                    capture_output=True, timeout=5, check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"Command isolation unavailable", result.stderr)
                self.assertFalse(self.record.exists())
'''
p.write_text(text[:start] + block + text[end:], encoding="utf-8")
