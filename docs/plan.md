# vmarket Implementation Plan

Agreed 2026-11-04. Vocabulary: [GLOSSARY.md](../GLOSSARY.md). Decisions: [docs/adr/](./adr/).

## Vision

vmarket sells isolated, self-service Instances of the owner's own Apps. A Customer picks an App, fills in its Inputs, pays by card, and gets a running Instance on their own subdomain within minutes — with zero manual work by the owner.

## System shape

- **Control plane:** FastAPI + SolidJS + SQLite/Litestream, one container on Fly — the same pattern as the Apps it sells.
- **Apps** declare a Manifest; vmarket is a generic engine with no app-specific code (ADR 0002).
- **Instances:** one per Customer — own Fly app, volume, SQLite DB, R2 backup, subdomain — in a dedicated Fly organization (ADR 0001).
- **Single image per App;** all customer-specific data enters via the runtime Provisioning Payload (ADR 0003).
- **Releases:** git tag → CI builds image → Security Gate (blocking scans) → push to registry → call vmarket release API → canary → automatic rollout to all Instances (ADR 0004).

## Key integrations and facts

- **Stripe (CZ):** 1.5% + 6.50 CZK per standard EEA card + ~0.7% Billing fee. MVP: monthly subscriptions only, no trial. Provisioning happens strictly after the payment webhook. Dunning → Suspend → delete after 30 days (last backup kept).
- **Fly costs:** running Instance (512 MB + 1 GB volume) ≈ $3–4/month; suspended ≈ $0.30/month.
- **Domain `vitekdvorak.cz`:** registrar Forpsi, DNS currently at Forpsi (no usable API) → move NS to Cloudflare (free). Root serves GitHub Pages (A 185.199.108.153); no MX/TXT records exist today.
- **URL scheme:** `store.vitekdvorak.cz` (marketplace), `<slug>.apps.vitekdvorak.cz` (Instances).
- **Email:** the Customer supplies SMTP credentials (host, port, user, password, sender) as Inputs; vmarket verifies them at order time and as a Provisioning step, and passes them to the Instance as Fly secrets (ADR 0005). No platform mail provider in the MVP.
- **Business:** OSVČ for now (s.r.o. when strangers pay), non-VAT-payer — must register as "identifikovaná osoba" (reverse charge on foreign services: Fly, Cloudflare). CZ customers only, so no OSS.
- **Customer accounts:** classic registration (email + password, verification, reset — reuse patterns from the zaci app). Billing self-service via Stripe Customer Portal; vmarket builds no billing UI.
- **Admin:** CLI only in MVP (register App, create Comped Instance, suspend/delete, status). Comped Instances have no Stripe objects and never suspend; only the owner creates them.
- **Observability:** Fly managed Prometheus/Grafana for platform metrics per Instance (free); `/metrics` endpoint required by the App Contract; vmarket probes every Instance externally each minute and stores uptime/latency history in its own DB; email alerts on thresholds; an external uptime service watches vmarket itself.
- **First App:** the driving-school app ("zaci") at `../dominik-web/app` — FastAPI + SolidJS + SQLite + Litestream, currently deployed as Fly app `ubouraka-zaci`. It stays in the dominik-web repo; vmarket consumes only its image and Manifest. The current customer becomes Comped Instance #1.

## Phases

### Phase 0 — Accounts & paperwork (parallel with Phase 1)

- [ ] Stripe account under the IČO (requires a public page with terms and contact)
- [ ] Move `vitekdvorak.cz` DNS to Cloudflare (copy the GitHub Pages records; registrar stays Forpsi)
- [ ] Dedicated Fly organization + scoped API token
- [ ] Cloudflare R2 bucket strategy + API token
- [ ] Accountant: register as identifikovaná osoba

### Phase 1 — Generalize the zaci app (de-risks the biggest unknown; start immediately)

- [ ] Runtime branding: `/api/branding` (name, logo, colors); frontend loads it at boot; backend serves logo/favicon/tokens from a branding directory with neutral built-in defaults; remove the build-time dependency on the WordPress theme assets
- [ ] Remove customer data from migrations (bank account, operator identity, phone, legal URLs) — schema only; neutral defaults in the settings row
- [ ] Provisioning Payload import on first boot (`/data/provision.json`): settings, course catalogue, branding, Operator account (created automatically, set-password link emailed); mark payload processed
- [ ] Move the hardcoded name out of code: mail SENDER + email texts, FastAPI title, PDF creator, iCal calendar name, frontend titles
- [ ] `/metrics` endpoint (Prometheus format)
- [ ] CI: build image, Security Gate scans (pip-audit/osv-scanner, semgrep, trivy — blocking), push to registry
- [ ] **Verification:** manually provision a second test instance with different branding end-to-end; ubouraka keeps running unchanged

### Phase 2 — Provisioning engine

- [ ] vmarket skeleton (FastAPI + SolidJS + SQLite/Litestream, same conventions as zaci)
- [ ] DB schema: customers, apps, releases, plans, instances, orders, probe history, event log
- [ ] Provisioner as an idempotent state machine with retries: Fly Machines API (app, volume, secrets, machine), Cloudflare DNS record, R2 bucket, payload upload, first-boot check
- [ ] Admin CLI: `app register`, `instance comp`, `instance suspend|delete`, `status`
- [ ] **Verification:** brother's instance provisioned via CLI as Comped = first real Instance through the engine

### Phase 3 — Selling (MVP complete)

- [ ] Catalogue web + order form generated from the Manifest's Inputs schema
- [ ] Customer registration + login
- [ ] Stripe Checkout + webhooks (`paid` → provision; `payment failed` → dunning → Suspend; `canceled` → schedule delete)
- [ ] Customer dashboard (list Instances, link to Stripe Customer Portal)
- [ ] Transactional emails (order confirmed, instance ready, payment issues)
- [ ] **Verification:** a full stranger-shaped purchase on a test card ends with a running Instance

### Phase 4 — Release pipeline

- [ ] Finalize `docs/app-contract.md`
- [ ] "vmarketize" Claude skill: walks an AI through adapting any existing app to the App Contract
- [ ] vmarket release API + canary rollout + rollback on failed health check
- [ ] Weekly re-scan of deployed images (new CVEs in old builds → alert)

### Phase 5 — Observability & launch

- [ ] External probes + history, Fly Prometheus polling, alert thresholds
- [ ] External uptime watchdog for vmarket itself
- [ ] Terms of service, first Plan price, store polish
- [ ] First paying customer

## Parked (deliberately not decided yet)

First Plan price · terms-of-service text · marketing polish · Telegram alerts · customer custom domains · one-time payments · public free trial/tier · admin web UI · s.r.o. conversion
