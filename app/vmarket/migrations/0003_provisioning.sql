-- Hodnoty Inputs pro Provisioning Payload (u placené Instance se kopírují z Order, u Comped je zadá vlastník).
-- Obrázky jsou v JSON jako {"$file": "<cesta>"} nebo base64 {"$base64": "..."}, viz provisioning/payload.py.
ALTER TABLE instances ADD COLUMN inputs_json TEXT NOT NULL DEFAULT '{}';

-- Postup Provisioningu po krocích: hotový krok se při opakování přeskočí. output_json nese výstupy kroků
-- (např. přístupy k bucketu), které další kroky potřebují.
CREATE TABLE provisioning_steps (
    instance_id INTEGER NOT NULL REFERENCES instances(id),
    step TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    output_json TEXT NOT NULL DEFAULT '{}',
    done_at TEXT,
    PRIMARY KEY (instance_id, step)
);
