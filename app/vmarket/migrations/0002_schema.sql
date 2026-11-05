CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT,
    email_verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- id je id z Manifestu (např. "autoskola"); manifest_json je poslední registrovaný Manifest.
CREATE TABLE apps (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    image TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE plans (
    id INTEGER PRIMARY KEY,
    app_id TEXT NOT NULL REFERENCES apps(id),
    plan_key TEXT NOT NULL,
    name TEXT NOT NULL,
    interval TEXT NOT NULL CHECK (interval IN ('month', 'year')),
    price_czk INTEGER CHECK (price_czk IS NULL OR price_czk >= 0), -- NULL = cena zatím není rozhodnutá
    stripe_price_id TEXT,
    UNIQUE (app_id, plan_key)
);

-- Release = nová verze App (tag). image_ref nese tag; scan_attestation_json je výsledek Security Gate.
CREATE TABLE releases (
    id INTEGER PRIMARY KEY,
    app_id TEXT NOT NULL REFERENCES apps(id),
    tag TEXT NOT NULL,
    image_ref TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    scan_attestation_json TEXT,
    status TEXT NOT NULL DEFAULT 'accepted' CHECK (status IN ('accepted', 'canary', 'rolled_out', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (app_id, tag)
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    app_id TEXT NOT NULL REFERENCES apps(id),
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    inputs_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'provisioned', 'canceled', 'failed')),
    stripe_session_id TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Comped Instance nemá Plan ani Order a nikdy se nesuspenduje (jen vlastník ji vytváří).
CREATE TABLE instances (
    id INTEGER PRIMARY KEY,
    app_id TEXT NOT NULL REFERENCES apps(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    slug TEXT NOT NULL UNIQUE CHECK (slug GLOB '[a-z0-9]*' AND slug NOT GLOB '*[^a-z0-9-]*'),
    billing_mode TEXT NOT NULL CHECK (billing_mode IN ('paid', 'comped')),
    plan_id INTEGER REFERENCES plans(id),
    order_id INTEGER REFERENCES orders(id),
    release_id INTEGER REFERENCES releases(id),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'provisioning', 'running', 'suspended', 'failed', 'deleted')),
    failure_reason TEXT,
    fly_app_name TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at TEXT,
    CHECK (billing_mode = 'comped' OR plan_id IS NOT NULL),
    CHECK (billing_mode = 'paid' OR (plan_id IS NULL AND order_id IS NULL))
);
CREATE INDEX instances_customer ON instances(customer_id);
CREATE INDEX instances_state ON instances(state);

ALTER TABLE orders ADD COLUMN instance_id INTEGER REFERENCES instances(id);

CREATE TABLE probe_history (
    id INTEGER PRIMARY KEY,
    instance_id INTEGER NOT NULL REFERENCES instances(id),
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
    status_code INTEGER,
    latency_ms INTEGER
);
CREATE INDEX probe_history_instance_at ON probe_history(instance_id, at);

ALTER TABLE event_log ADD COLUMN instance_id INTEGER REFERENCES instances(id);
CREATE INDEX event_log_instance ON event_log(instance_id, id);
