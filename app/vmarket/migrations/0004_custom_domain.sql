-- Vlastní doména zákazníka (např. zaci.ubouraka.cz). DNS záznam u zákazníka vmarket nezaloží; vystaví certifikát a řekne, kam má CNAME vést.
ALTER TABLE instances ADD COLUMN custom_domain TEXT;
CREATE UNIQUE INDEX instances_custom_domain ON instances(custom_domain) WHERE custom_domain IS NOT NULL;
