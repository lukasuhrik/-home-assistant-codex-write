# Home Assistant Codex Write

Private release-engineering repository for the **MVP WRITE V1** extension used by Lukas House.

## Pinned upstreams

- `moryoav/home-assistant-codex` tag `v0.1.70`
  - commit: `06ee7b0b77455119a0ffcbc79217e6ccda8a7128`
- `home-assistant/core` tag `2026.9.4`
  - commit: `9212531f40a0b7b23229a90d688dd79d9dfccff4`

## Goal

Build and verify a release candidate for the hybrid write architecture:

- worker: MCP/capability enforcement
- Home Assistant integration: HA-native registry/service backend
- browser verification remains read-only
- no direct edits of Home Assistant registry `.storage` files

## Release gate

Production deployment is forbidden until all required gates pass.
See `docs/RELEASE_GATE.md`.

## Current status

This repository is the isolated build/test environment. It does **not** deploy to Lukas House.