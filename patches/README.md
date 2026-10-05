# Patch import

The canonical MVP WRITE V1 implementation currently lives in the Lukas House dev workspace at:

`/config/dev/mvp-write-v1`

Do not reconstruct it from prose. Export the tested snapshot/diff and import it here as:

- `patches/mvp-write-v1.patch`
- `patches/mvp-write-v1.sha256`

The CI pipeline will not run the full release-candidate gate until that exact tested patch is present.