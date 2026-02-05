# Project Titan v6.0

Local-first token scoring platform with a modular monolith (FastAPI) and worker processes. All services run via Docker Compose.

## Stack
- **Backend:** Python 3.11, FastAPI, SQLAlchemy (async), Alembic
- **Database:** Postgres 16
- **Queue:** Redis Streams
- **Frontend:** Next.js 14, Tailwind, shadcn/ui-inspired components
- **LLM Narration:** Ollama (local, optional)

## Quickstart

```bash
cp .env.example .env
```

Update `.env` with real RPC credentials and confirm `CHAIN_CONFIG` has entries for **ethereum** and **bsc**.

### Start everything

```bash
docker compose up --build
```

Services will be available:
- API: http://localhost:8000
- Web: http://localhost:3000
- Ollama: http://localhost:11434

### Dashboard usage

Once the stack is running, open http://localhost:3000 to access the dashboard. The UI includes:
- **Live Feed**: recent alerts with TSS, conviction, and the current regime.
- **Shadow Pool**: wallet metrics filtered by tier (ocean/shadow/titan/ignore).
- **Token Scanner**: lookup for token risk components + latest alerts for a token.

You can point the frontend at a different API host by setting `NEXT_PUBLIC_API_BASE_URL`
in `frontend/.env.local` (default: `http://localhost:8000`).

### Run migrations

```bash
docker compose exec api alembic upgrade head
```

## Seed pack warm-start

Warm-start seed packs are loaded from three CSVs placed either at the repo root
or in a `/seed_pack` directory:

- `watched_pools.csv`
- `seed_wallets.csv`
- `ignore_list.csv`

Run the import with:

```bash
make seed_import
```

This uses the existing `wallets` table for ignore enforcement (`tier=ignore`), which
keeps ingestion checks O(1) via the primary key lookup on `(chain, address)` and avoids
introducing another table to keep in sync. Ignored wallets are skipped during decoding
and never promoted by downstream workers.

## Titan v8.0 schema additions

The v8.0 schema adds targeted tables and wallet metadata to support watch lists
and signal outcome evaluation. Tradeable performance fields in `signal_outcomes`
store decimal fractions (e.g., `0.25` = 25%).

### New tables
- **watch_pairs**: per-chain DEX pair watchlist with priority and TTL fields (`expires_at`).
- **signal_outcomes**: per-alert, per-horizon outcome metrics for sellability and tradeable returns.

### Wallet metadata additions
- **wallets**: `source`, `prior_weight`, `merit_score`, `tier`, `tier_reason`, `ignore_reason` plus indexes on tier and merit score.

### View logs

```bash
docker compose logs -f
```

### Health check

```bash
curl http://localhost:8000/health
```

### Read-only endpoints

```bash
curl "http://localhost:8000/alerts?limit=25&offset=0&chain=ethereum"
curl "http://localhost:8000/alerts/<alert-id>"
curl "http://localhost:8000/wallets?tier=shadow"
curl "http://localhost:8000/wallets/<wallet-address>"
curl "http://localhost:8000/tokens/ethereum/<token-address>/risk"
curl "http://localhost:8000/regime"
```

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `POSTGRES_USER` | Postgres user | `titan` |
| `POSTGRES_PASSWORD` | Postgres password | `titan` |
| `POSTGRES_DB` | Postgres database | `titan` |
| `DATABASE_URL` | Postgres connection string | `postgresql+asyncpg://titan:titan@db:5432/titan` |
| `REDIS_URL` | Redis connection string | `redis://redis:6379/0` |
| `OLLAMA_URL` | Ollama base URL | `http://ollama:11434` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.1` |
| `ALCHEMY_WS_URL` | Ethereum WebSocket endpoint | none |
| `ALCHEMY_HTTP_URL` | Ethereum HTTP endpoint | none |
| `BSC_WS_URL` | BSC WebSocket endpoint | none |
| `BSC_HTTP_URL` | BSC HTTP endpoint | none |
| `CHAIN_CONFIG` | JSON map of chain settings | required |
| `WATCHED_ADDRESSES_ETH` | (Deprecated) manual Ethereum addresses | none |
| `WATCHED_ADDRESSES_BSC` | (Deprecated) manual BSC addresses | none |
| `AUTOPILOT_LIQUIDITY_FLOOR_ETH` | Autopilot minimum liquidity (USD) for Ethereum pairs | `50000` |
| `AUTOPILOT_LIQUIDITY_FLOOR_BSC` | Autopilot minimum liquidity (USD) for BSC pairs | `25000` |
| `AUTOPILOT_VOLUME_FLOOR_24H` | Autopilot minimum 24h volume (USD) | `50000` |
| `AUTOPILOT_MIN_AGE_HOURS` | Autopilot minimum pair age when DexScreener reports creation time | `1.0` |
| `AUTOPILOT_AGE_FALLBACK_MULTIPLIER` | Multiplier applied to liquidity/volume floors when age is unknown | `1.5` |
| `AUTOPILOT_MAX_PAIRS_PER_CHAIN` | Max active autopilot watch pairs per chain | `200` |
| `AUTOPILOT_MIN_SLEEP_SECONDS` | Autopilot minimum sleep between runs | `600` |
| `AUTOPILOT_MAX_SLEEP_SECONDS` | Autopilot maximum sleep between runs | `1800` |
| `DEXSCREENER_BASE_URL` | DexScreener API | `https://api.dexscreener.com/latest/dex` |
| `GOPLUS_BASE_URL` | GoPlus API | `https://api.gopluslabs.io/api/v1` |
| `LOG_LEVEL` | Log level | `info` |

## API usage

### Score a token

```bash
curl -X POST http://localhost:8000/score \
  -H 'Content-Type: application/json' \
  -d '{"token_address": "0x0000000000000000000000000000000000000000", "chain": "ethereum"}'
```

### Narrate structured reasons

```bash
curl -X POST http://localhost:8000/narrate \
  -H 'Content-Type: application/json' \
  -d '{"reasons": [{"source": "goplus", "message": "Example", "severity": "low", "data": {}}]}'
```

## Worker

The worker consumes Redis Stream entries from `score_jobs` and persists scores to Postgres.

```bash
redis-cli XADD score_jobs * token_address 0x0000000000000000000000000000000000000000 chain ethereum
```

## Watching addresses for EVM logs

The EVM listener subscribes to **active** watch pairs stored in Postgres. Those
pairs are populated by the seed pack (`watch_pairs` source = `seed_pack`) and by
the watchlist autopilot worker (`source=autopilot`). The listener snapshots the
active watch set (TTL + seed pack anchors) via Redis to avoid DB thrash.

To tune the autopilot, adjust the `AUTOPILOT_*` environment variables and the
per-chain caps/floors in `.env`.

## Smoke tests

Run these after `docker compose up --build`:

```bash
make smoke
```

Or run them individually:

```bash
./scripts/smoke_api.sh
./scripts/smoke_worker.sh
./scripts/smoke_web.sh
```

You can also run the backend API smoke script after seeding alerts:

```bash
docker compose exec api python -m app.scripts.smoke_alerts
docker compose exec api python -m app.scripts.smoke_api
docker compose exec api python -m app.scripts.smoke_autopilot
```

## Troubleshooting

### Smoke tests fail

* Ensure the database is fresh: `make reset_db && make migrate`.
* If the decoder smoke test fails repeatedly, inspect the dead-letter stream
  `titan:raw_events:dead` in Redis to see payloads that could not be decoded.
* If DexScreener or GoPlus requests fail, the services enter a short circuit-breaker
  cooldown to avoid spamming upstream APIs. Wait for the cooldown and retry.

## Architecture notes
- Deterministic scoring first. LLM narration only summarizes structured reasons and never alters the score.
- Aggressive caching (Redis) for DexScreener + GoPlus (no historical RPC scraping).
- Structured JSON logging and exponential backoff retries for external APIs.
