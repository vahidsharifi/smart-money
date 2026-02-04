#!/usr/bin/env bash
set -euo pipefail

curl -sSf http://localhost:3000 >/dev/null

echo "Web smoke test passed."
