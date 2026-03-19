#!/usr/bin/env bash
set -euo pipefail
echo "=== Sentinel Health Check ==="
echo -n "Engine: "
curl -sf http://localhost:8100/health | python3 -m json.tool || echo "UNREACHABLE"
echo -n "Ready: "
curl -sf http://localhost:8100/ready | python3 -m json.tool || echo "NOT READY"
