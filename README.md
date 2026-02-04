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

## Smoke tests

Run these after `docker compose up --build`:

```bash
./scripts/smoke_api.sh
./scripts/smoke_worker.sh
./scripts/smoke_web.sh
```

## Architecture notes
- Deterministic scoring first. LLM narration only summarizes structured reasons and never alters the score.
- Aggressive caching (Redis) for DexScreener + GoPlus (no historical RPC scraping).
- Structured JSON logging and exponential backoff retries for external APIs.
