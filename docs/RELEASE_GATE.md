# MVP WRITE V1 release gate

## Hard rule

**No production deployment to Lukas House until every required gate is green.**

Pinned inputs:

- Home Assistant Codex `v0.1.70` — `06ee7b0b77455119a0ffcbc79217e6ccda8a7128`
- Home Assistant Core `2026.9.4` — `9212531f40a0b7b23229a90d688dd79d9dfccff4`

## Required evidence

- [x] Upstream tags resolved to exact commits
- [ ] MVP dev snapshot imported as reproducible patch
- [ ] Patch checksum verified
- [ ] Existing 58 tests reproduced in CI
- [ ] Core 2026.9.4 real contract tests
- [ ] Integration endpoint test in a real HA harness
- [ ] Protocol handshake
- [ ] Add-on option/scope schema
- [ ] No blocking fsync/I/O in HA event loop
- [ ] Durable operation status/recovery
- [ ] Worker container build
- [ ] Worker startup/import smoke test
- [ ] Existing read/dashboard regression suite
- [ ] Expanded security suite
- [ ] Crash/timeout recovery suite
- [ ] Phase 1A simulation twice with second run NO_CHANGE/ALREADY_EXISTS
- [ ] Rollback artifacts

## Production defaults

- write scopes OFF
- browser remains read-only
- WRITE_SERVICE OFF for first deployment
- no direct registry `.storage` edits
- no automatic Core/worker restart