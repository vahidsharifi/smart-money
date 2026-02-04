#!/usr/bin/env bash
set -euo pipefail

redis-cli XADD score_jobs * token_address 0x0000000000000000000000000000000000000000 chain ethereum >/dev/null
sleep 2

echo "Worker job enqueued. Check worker logs for processing."
