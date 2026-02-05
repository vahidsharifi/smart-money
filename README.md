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
| `WATCHED_ADDRESSES_ETH` | Ethereum addresses to watch | none |
| `WATCHED_ADDRESSES_BSC` | BSC addresses to watch | none |
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

The EVM listener only subscribes to explicit watch lists. Provide comma-separated
addresses (or a JSON array) per chain in your `.env`:

```bash
WATCHED_ADDRESSES_ETH=0xabc...,0xdef...
WATCHED_ADDRESSES_BSC=["0x123...", "0x456..."]
```

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
