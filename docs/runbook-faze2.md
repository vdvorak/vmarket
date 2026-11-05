# Runbook: ověření fáze 2 (issue #6)

Cíl: provést přes `python -m vmarket.cli` první Instance na skutečných službách. Nejdřív zkušební autoškola, pak skutečná.

## 1. Předpoklady (jednorázově, ručně)

**Kód a image**
1. Pushnout `main` v `vmarket` a `dominik-web`.
2. V `dominik-web`: `git tag app-v0.1.0 && git push --tags` (CI: testy, skeny, push do GHCR).
3. GitHub → Packages → `autoskola-app` → Package settings → Visibility: **Public** (adaptér Fly umí jen veřejný image).
4. Kontrola: `docker manifest inspect ghcr.io/vdvorak/autoskola-app:app-v0.1.0`.

**Fly**: `fly orgs create vmarket-instances`, pak `fly tokens create org vmarket-instances` → `FLY_API_TOKEN`; `FLY_ORG_SLUG` je slug organizace.

**Cloudflare**
1. Přidat doménu `vitekdvorak.cz` (Free), zkontrolovat přenesené záznamy (hlavně A kořene `185.199.108.153` pro GitHub Pages).
2. U Forpsi přepnout nameservery na ty od Cloudflare, počkat na aktivaci zóny.
3. Opsat Zone ID (`CLOUDFLARE_ZONE_ID`) a Account ID (`CLOUDFLARE_ACCOUNT_ID`).
4. Zapnout R2 (chce kartu).
5. Tokeny: DNS (Zone → DNS → Edit, jen tato zóna) → `CLOUDFLARE_DNS_TOKEN`; R2 (Account → Workers R2 Storage → Edit + Account → API Tokens → Edit) → `CLOUDFLARE_R2_TOKEN`.

**Resend**
1. Domains → Add `mail.vitekdvorak.cz` (region EU), DNS záznamy přidat v Cloudflare jako DNS only, počkat na Verified.
2. API key s plným přístupem → `RESEND_API_KEY`; ID domény → `RESEND_DOMAIN_ID` (nepovinné).

## 2. Proměnné prostředí

```sh
export FLY_API_TOKEN=… FLY_ORG_SLUG=vmarket-instances
export CLOUDFLARE_DNS_TOKEN=… CLOUDFLARE_ZONE_ID=… CLOUDFLARE_ACCOUNT_ID=… CLOUDFLARE_R2_TOKEN=…
export RESEND_API_KEY=… RESEND_DOMAIN=mail.vitekdvorak.cz RESEND_DOMAIN_ID=…
export VMARKET_BASE_DOMAIN=apps.vitekdvorak.cz
export DATABASE_PATH=$HOME/vmarket-dev.db
```

## 3. Zkušební Instance

```sh
cd app && uv sync
uv run python -m vmarket.cli app register ../../dominik-web/app/vmarket.yaml
uv run python -m vmarket.cli release add autoskola app-v0.1.0
cp ../docs/examples/* /tmp/ && $EDITOR /tmp/inputs-testova.json     # operator_email na vaši adresu
uv run python -m vmarket.cli instance comp --app autoskola --slug testova --email vy@example.cz --inputs /tmp/inputs-testova.json
uv run python -m vmarket.cli status testova
```

Při `failed` opravit příčinu z výpisu a spustit `instance provision testova` (hotové kroky se přeskočí).

Ověřit:
- `https://testova.apps.vitekdvorak.cz/api/health` vrací ok,
- v prohlížeči jiný název, logo a barvy,
- Provozovateli přišel e-mail s odkazem na heslo (i spam),
- metriky na portu 9091: `fly ssh console -a vm-autoskola-testova -C "python -c \"import urllib.request as u;print(u.urlopen('http://localhost:9091/metrics').read()[:200])\""`,
- `ubouraka-zaci` běží beze změny.

Úklid: `instance delete testova` (zruší Fly app a DNS; R2 bucket `vm-autoskola-testova` smazat ručně v Cloudflare).

## 4. Skutečná Instance (Comped)

Stejný postup se skutečnými daty a slugem. Instance #1 (`ubouraka-zaci`) zůstává ručně spravovaná a vmarket se jí nedotkne.

## 5. Po ověření

Zapsat do issue #6, co selhalo nebo bylo ručně otravné, a issue zavřít.

Neověřené předpoklady adaptérů: logy z Fly (`recent_logs`), pole `domain_id` u API klíče Resendu, SMTP údaje Resendu, název R2 oprávnění.
