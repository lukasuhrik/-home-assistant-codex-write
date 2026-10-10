#!/usr/bin/env python3
from pathlib import Path
import runpy, py_compile

ROOT = Path(__file__).resolve().parents[1]
W = ROOT / "codex_cli_worker_trusted"

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

# Replace the shell wrapper with a native ELF wrapper. Some HAOS/Codex
# descriptor-backed mounts are inherited across exec and can be disturbed by
# an intermediate script interpreter. The native wrapper validates the same
# minimum isolation contract, unsets secret env vars, injects network/cap-drop
# hardening, and execs the real bwrap without opening/closing unrelated FDs.
c = W / "codex-bwrap.c"
c.write_text(r'''#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *REAL_BWRAP = "/opt/codex-sandbox/bwrap.real";

static int is_proc_probe(int argc, char **argv) {
    int user_ns=0, pid_ns=0, proc_mount=0, sep=-1;
    for (int i=1;i<argc;i++) {
        if (!strcmp(argv[i],"--unshare-user")) user_ns=1;
        else if (!strcmp(argv[i],"--unshare-pid")) pid_ns=1;
        else if (!strcmp(argv[i],"--proc")) {
            if (i+1>=argc || strcmp(argv[i+1],"/proc")) return 0;
            proc_mount=1; i++;
        } else if (!strcmp(argv[i],"--")) { sep=i; break; }
    }
    if (!(user_ns && pid_ns && proc_mount) || sep < 0) return 0;
    if (argc != sep + 2) return 0;
    return !strcmp(argv[sep+1],"/bin/true") || !strcmp(argv[sep+1],"/usr/bin/true");
}

static void exec_real(int argc, char **argv) {
    char **out = calloc((size_t)argc + 1, sizeof(char *));
    if (!out) { perror("calloc"); _exit(126); }
    out[0] = (char *)REAL_BWRAP;
    for (int i=1;i<argc;i++) out[i]=argv[i];
    out[argc]=NULL;
    execv(REAL_BWRAP,out);
    perror("execv bwrap.real");
    _exit(126);
}

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1],"--version")) exec_real(argc,argv);

    if (is_proc_probe(argc,argv)) {
        char **out = calloc((size_t)argc + 2, sizeof(char *));
        if (!out) { perror("calloc"); return 126; }
        out[0]="/usr/bin/python3";
        out[1]="/opt/codex-sandbox/codex-bwrap-probe.py";
        for (int i=1;i<argc;i++) out[i+1]=argv[i];
        out[argc+1]=NULL;
        execv(out[0],out);
        perror("execv probe");
        return 126;
    }

    if (argc >= 2 && !strcmp(argv[1],"--help")) {
        char *out[]={(char*)REAL_BWRAP,"--cap-drop","ALL","--help",NULL};
        execv(REAL_BWRAP,out);
        perror("execv help");
        return 126;
    }

    int user_ns=0,pid_ns=0,sep=-1;
    for (int i=1;i<argc;i++) {
        if (!strcmp(argv[i],"--unshare-user")) user_ns=1;
        else if (!strcmp(argv[i],"--unshare-pid")) pid_ns=1;
        else if (!strcmp(argv[i],"--")) { sep=i; break; }
    }
    if (!(user_ns && pid_ns) || sep < 0 || sep == argc-1) {
        fputs("Command isolation unavailable; refusing execution\n", stderr);
        return 1;
    }

    unsetenv("HA_VERIFICATION_CAPABILITY");
    unsetenv("SUPERVISOR_TOKEN");
    unsetenv("HASSIO_TOKEN");
    unsetenv("HA_TOKEN");
    unsetenv("OPENAI_API_KEY");
    unsetenv("CODEX_API_KEY");

    static char *extra[]={"--unshare-net","--cap-drop","ALL"};
    int extra_n=3;
    char **out=calloc((size_t)argc + extra_n + 1,sizeof(char *));
    if (!out) { perror("calloc"); return 126; }
    int k=0;
    out[k++]=(char*)REAL_BWRAP;
    for (int i=0;i<extra_n;i++) out[k++]=extra[i];
    for (int i=1;i<argc;i++) out[k++]=argv[i];
    out[k]=NULL;

    execv(REAL_BWRAP,out);
    perror("execv bwrap.real");
    return errno == ENOENT ? 127 : 126;
}
''', encoding="utf-8")

# Docker build compiles the native wrapper, then removes the compiler toolchain.
p = W / "Dockerfile"
text = p.read_text(encoding="utf-8")
old = '''COPY codex-bwrap /opt/codex-sandbox/bwrap
COPY codex-bwrap-probe.py /opt/codex-sandbox/codex-bwrap-probe.py

RUN chmod 0755 /opt/codex-sandbox/bwrap
'''
new = '''COPY codex-bwrap.c /tmp/codex-bwrap.c
COPY codex-bwrap-probe.py /opt/codex-sandbox/codex-bwrap-probe.py

RUN apk add --no-cache --virtual .codex-build-deps build-base \
    && cc -O2 -Wall -Wextra -o /opt/codex-sandbox/bwrap /tmp/codex-bwrap.c \
    && chmod 0755 /opt/codex-sandbox/bwrap \
    && rm -f /tmp/codex-bwrap.c \
    && apk del .codex-build-deps
'''
if old not in text:
    raise SystemExit("expected V6 Docker wrapper block not found")
p.write_text(text.replace(old,new,1),encoding="utf-8")

# V7 tests should exercise the compiled-wrapper source contract in addition to
# the existing behavioral suite used for V6.
p=W/"tests/test_managed_isolation.py"
text=p.read_text(encoding="utf-8")
marker="\n    def test_probe_never_launches_nested_codex"
idx=text.index(marker)
extra='''

    def test_v7_native_wrapper_preserves_descriptor_model(self):
        source = (WORKER / "codex-bwrap.c").read_text()
        docker = (WORKER / "Dockerfile").read_text()
        self.assertIn('execv(REAL_BWRAP,out)', source)
        self.assertIn('unsetenv("SUPERVISOR_TOKEN")', source)
        self.assertIn('"--unshare-net","--cap-drop","ALL"', source)
        self.assertIn('cc -O2 -Wall -Wextra -o /opt/codex-sandbox/bwrap', docker)
        self.assertNotIn('COPY codex-bwrap /opt/codex-sandbox/bwrap', docker)
'''
p.write_text(text[:idx]+extra+text[idx:],encoding="utf-8")

for rel in (
    "codex_cli_worker_trusted/tests/test_managed_isolation.py",
):
    py_compile.compile(str(ROOT / rel), doraise=True)

print("HAIPC3 V7 transform applied")
