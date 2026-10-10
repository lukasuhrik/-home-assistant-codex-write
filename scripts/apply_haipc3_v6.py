#!/usr/bin/env python3
from pathlib import Path
import runpy, py_compile

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "codex_cli_worker_trusted"

runpy.run_path(str(ROOT / "scripts/apply_haipc3_v5.py"), run_name="__main__")

def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected V5 block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

replace("codex_cli_worker_trusted/config.yaml", 'version: "0.1.72-haipc3"', 'version: "0.1.73-haipc3"')
replace("codex_cli_worker_trusted/verification_mcp.py", '"version": "0.1.72-haipc3"', '"version": "0.1.73-haipc3"')
replace("scripts/validate_candidate.py", "assert config['version'] == '0.1.72-haipc3'", "assert config['version'] == '0.1.73-haipc3'")

# Preserve Codex descriptor-backed mounts. Normal commands now validate in the
# shell wrapper and exec real bwrap directly, avoiding an intermediate Python
# exec that can invalidate descriptor-backed --file/--ro-bind-data mounts.
wrapper = W / "codex-bwrap"
wrapper.write_text('''#!/bin/sh
set -eu

REAL_BWRAP=/opt/codex-sandbox/bwrap.real

if [ "${1:-}" = "--version" ]; then
  exec "$REAL_BWRAP" "$@"
fi

is_codex_proc_probe() (
  user_ns=0 pid_ns=0 proc_mount=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --unshare-user) user_ns=1 ;;
      --unshare-pid) pid_ns=1 ;;
      --proc)
        [ "$#" -ge 2 ] && [ "$2" = /proc ] || return 1
        proc_mount=1
        shift
        ;;
      --)
        shift
        [ "$#" -eq 1 ] || return 1
        case "$1" in
          /bin/true|/usr/bin/true)
            [ "$user_ns$pid_ns$proc_mount" = 111 ]
            return
            ;;
          *) return 1 ;;
        esac
        ;;
    esac
    shift
  done
  return 1
)

if is_codex_proc_probe "$@"; then
  exec /usr/bin/python3 /opt/codex-sandbox/codex-bwrap-probe.py "$@"
fi

if [ "${1:-}" = "--help" ]; then
  exec "$REAL_BWRAP" --cap-drop ALL --help
fi

user_ns=0
pid_ns=0
seen_sep=0
for arg in "$@"; do
  if [ "$seen_sep" -eq 0 ]; then
    case "$arg" in
      --unshare-user) user_ns=1 ;;
      --unshare-pid) pid_ns=1 ;;
      --) seen_sep=1 ;;
    esac
  fi
done

if [ "$seen_sep$user_ns$pid_ns" != 111 ]; then
  echo "Command isolation unavailable; refusing execution" >&2
  exit 1
fi

exec "$REAL_BWRAP" \
  --unshare-net \
  --cap-drop ALL \
  --unsetenv HA_VERIFICATION_CAPABILITY \
  --unsetenv SUPERVISOR_TOKEN \
  --unsetenv HASSIO_TOKEN \
  --unsetenv HA_TOKEN \
  --unsetenv OPENAI_API_KEY \
  --unsetenv CODEX_API_KEY \
  "$@"
''', encoding="utf-8")
wrapper.chmod(0o755)

# Add regression asserting a descriptor-backed normal command survives wrapper.
p = W / "tests/test_bwrap.py"
text = p.read_text(encoding="utf-8")
marker = "\n    def test_probe_preserves_signal_termination"
idx = text.index(marker)
extra = '''
    def test_normal_path_preserves_inherited_mount_descriptor(self) -> None:
        """Normal guarded commands must preserve descriptor-backed bwrap mounts."""
        with tempfile.TemporaryFile() as file:
            file.write(b"normal mount descriptor")
            file.seek(0)
            args = [*PROBE[:-1], "/bin/echo"]
            result = subprocess.run(
                [str(self.wrapper), *args],
                env={**os.environ, "TEST_RECORD": str(self.record), "TEST_FD": str(file.fileno())},
                pass_fds=(file.fileno(),), capture_output=True, timeout=5,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.record.read_text())["fd"], "normal mount descriptor")
'''
p.write_text(text[:idx] + extra + text[idx:], encoding="utf-8")

# V6 wrapper no longer invokes command_guard.py on the runtime path, but keep
# command_guard.py and its unit tests as a reference implementation.
for rel in ("codex_cli_worker_trusted/tests/test_bwrap.py",):
    py_compile.compile(str(ROOT / rel), doraise=True)

print("HAIPC3 V6 transform applied")
