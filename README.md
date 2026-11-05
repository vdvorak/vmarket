# vmarket
## Vývoj

Řídicí rovina je v `app/` (FastAPI + SQLite/Litestream, jeden kontejner). Vše běží v Dockeru:

- `make test` — pytest (`ARGS="-k health"` zúží výběr)
- `make lint` / `make fix` — ruff + mypy
- `make check` — lint + testy (to samé běží v CI)
- `make image` — runtime image; `/api/health` na portu 8000, metriky na interním portu 9091
