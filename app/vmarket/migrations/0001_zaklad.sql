-- Zatím jen základ; schéma (customers, apps, releases, instances, …) přidá issue "DB schéma".
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
