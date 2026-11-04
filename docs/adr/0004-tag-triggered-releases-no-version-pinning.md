# Tag-triggered Releases, canary rollout, no version pinning

A git tag in an App's repository triggers CI to build and push the image and announce a Release to vmarket, which rolls it out to all of that App's Instances automatically — one canary Instance first, the rest after its health check passes. Instances cannot stay on old versions. This forces Apps to keep migrations backward-compatible, but keeps operations and support single-version, which the zero-ops goal outweighs; per-customer version freedom was rejected as a support and migration burden.
