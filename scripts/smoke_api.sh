#!/usr/bin/env bash
set -euo pipefail

curl -sSf http://localhost:8000/health >/dev/null
curl -sSf -X POST http://localhost:8000/score \
  -H 'Content-Type: application/json' \
  -d '{"token_address": "0x0000000000000000000000000000000000000000", "chain": "ethereum"}' \
  >/dev/null

echo "API smoke test passed."
