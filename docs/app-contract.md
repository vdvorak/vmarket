# App Contract (v0 — draft)

What an App must satisfy to be registered in vmarket. This draft guides Phase 1; it is finalized in Phase 4 together with the "vmarketize" skill. Vocabulary: [GLOSSARY.md](../GLOSSARY.md).

## Packaging

- One docker image serves every Instance; nothing customer-specific is baked in at build time (ADR 0003).
- The image is self-contained: building it must not require files from outside the App's own directory tree.
- Exactly one machine per Instance; the App must not assume horizontal scaling.
- Container runs as a non-root user.

## Configuration

- All configuration comes from environment variables.
- Data lives in SQLite under a single data directory (default `/data`), WAL mode.
- Litestream conventions: honors `LITESTREAM_REPLICA_URL` + `LITESTREAM_S3_*`; restores the DB from the replica on boot when the local file is missing.
- Outbound email via SMTP from env (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`); the App must behave sanely when SMTP is unconfigured.

## Provisioning

- On first boot, if `/data/provision.json` exists and is unprocessed, the App imports it: business settings, catalogue data, branding, and the Operator account; then marks it processed (idempotent).
- The Operator receives a set-password link by email; no manual bootstrap (no SSH, no CLI) may be required.
- Database migrations contain schema only — never customer or demo data.

## Branding

- Display name, logo, favicon, and color tokens are runtime values from the Provisioning Payload, with neutral built-in defaults.
- No build-time branding assets.

## Operations

- `GET /api/health`: liveness + readiness; used by Fly checks and by canary rollout.
- `GET /metrics`: Prometheus format; at minimum request counts and latencies, plus app-level counters worth alerting on.
- The App tolerates stop/start at any time (Suspend/resume) without data loss.
- Backward-compatible migrations: Instance N-1 data must survive Release N (no version pinning, ADR 0004).

## Security

- Passwords hashed with argon2; server-side sessions; secure cookies in production.
- Rate limiting on authentication endpoints.
- No secrets in the image or repository; secrets arrive via env (Fly secrets).
- HTTPS-only behind the platform proxy; honors the platform client-IP header.

## Release (Security Gate)

- CI on git tag: dependency audit (pip-audit/osv-scanner, npm audit), static analysis (semgrep/bandit), image scan (trivy). HIGH/CRITICAL findings block the Release.
- CI pushes the image and calls the vmarket release API with the image reference, the Manifest, and scan attestation.

## Manifest

- A `vmarket.yaml` at the App's root declares: identity (id, name, description), image reference, the Inputs schema (typed: text, image, color, money, list/table), Plans, and resource requirements (RAM, volume size).
- The Inputs schema is what generates the order form and the Provisioning Payload; it is the single source of truth for what a Customer must supply.
