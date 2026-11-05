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

### Manifest format (v1)

First implemented by the autoskola App (`app/vmarket.yaml` in dominik-web); this is the reference shape. Top-level keys:

- `manifest_version`, `id`, `name`, `description`, `image` (no tag; the Release adds it).
- `provisioning`: `payload_file`, `payload_version`, `branding_dir`, `branding_defaults_dir` (directory in the image with the App's neutral default branding; required when an Input has type `color_tokens`, because the provisioner rewrites the tokens in the default `tokens.css` read from there). The payload format itself is owned by the App (documented in its README) and versioned by `payload_version`.
- `env` (optional): static environment variables every Instance gets (for example `SECURE_COOKIES`, `CLIENT_IP_HEADER`). vmarket adds the per-Instance ones itself (`APP_URL`, SMTP, Litestream).
- `resources`: `machines` (must be 1), `memory_mb`, `volume_gb`, `volume_mount`, `port`, `health` (`path`, `grace_period_s`), `metrics` (`port`, `path`).
- `plans`: list of `id`, `name`, `interval`, `price_czk` (`null` = TBD until pricing is decided).
- `inputs`: list of typed fields. Each has `id`, `type`, `label` (shown to the Customer, in the App's language), optional `help`, `required`, `default`, `validation`, and `target`.
- `payload_constants`: fixed payload values the Customer does not enter (for example `version`).

Input types: `text`, `email`, `url`, `number`, `money` (with `currency`), `boolean`, `image`, `color_tokens` (a group of `color` fields, each mapped to a CSS token), `table`. A `table` has `columns` (same field shape, `target` is relative to a row) and `min_rows`/`max_rows`. Rows that share a parent key are grouped by the provisioner (autoskola: `courses` rows with the same `course_slug` become one course with `variants[]`).

`validation` keys: `min_length`, `max_length`, `pattern` (+ `message`), `min`, `max`, `integer`, `one_of`, `schemes` (url), `formats`/`min_width_px`/`max_size_kb` (image), `min_rows`/`max_rows`. The same rules must be enforced by the App when it imports the payload; the manifest is stricter or equal, never looser.

`target` says where the value lands: `payload:<dotted.path>` is a key in `provision.json`; `branding:<file>` is a file the provisioner writes straight into `branding_dir` (`branding:branding.json#name` is a key inside a JSON file the provisioner assembles). Branding therefore never travels in the payload, matching the App Contract's Branding section.

