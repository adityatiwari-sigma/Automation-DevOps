#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# NETWORK VALIDATION — verifies the remote server can reach
# the monitoring stack (Prometheus, Loki) and its own gateway.
# Used by generic_triage.sh to rule out network-level root causes.
# ============================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
CONFIG_FILE="$(dirname "$DIR")/config.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Read config ────────────────────────────────────────────
LOCAL_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['network']['local_ip'])")
REMOTE_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['network']['remote_ip'])")
PROM_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['ports']['prometheus'])")
LOKI_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['ports']['loki'])")

echo "[$TIMESTAMP] TRIGGER=\"validate_network\" ACTION=\"connectivity-check\" STATUS=\"STARTING\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"

FAILURES=0

# ── Check 1: remote → local Prometheus ────────────────────
PROM_RESULT=$(python3 "$DIR/ssh_helper.py" \
    "curl -sf --connect-timeout 5 http://${LOCAL_IP}:${PROM_PORT}/-/healthy && echo OK || echo FAIL" \
    2>/dev/null || echo "FAIL")
if [ "$PROM_RESULT" = "OK" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" CHECK=\"prometheus-reachable\" STATUS=\"PASS\" ENDPOINT=\"${LOCAL_IP}:${PROM_PORT}\"" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" CHECK=\"prometheus-reachable\" STATUS=\"FAIL\" ENDPOINT=\"${LOCAL_IP}:${PROM_PORT}\"" >> "$LOG_FILE"
    echo "WARNING: Remote server cannot reach Prometheus at ${LOCAL_IP}:${PROM_PORT}" >&2
    FAILURES=$((FAILURES + 1))
fi

# ── Check 2: remote → local Loki ──────────────────────────
LOKI_RESULT=$(python3 "$DIR/ssh_helper.py" \
    "curl -sf --connect-timeout 5 http://${LOCAL_IP}:${LOKI_PORT}/ready && echo OK || echo FAIL" \
    2>/dev/null || echo "FAIL")
if [ "$LOKI_RESULT" = "OK" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" CHECK=\"loki-reachable\" STATUS=\"PASS\" ENDPOINT=\"${LOCAL_IP}:${LOKI_PORT}\"" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" CHECK=\"loki-reachable\" STATUS=\"FAIL\" ENDPOINT=\"${LOCAL_IP}:${LOKI_PORT}\"" >> "$LOG_FILE"
    echo "WARNING: Remote server cannot reach Loki at ${LOCAL_IP}:${LOKI_PORT}" >&2
    FAILURES=$((FAILURES + 1))
fi

# ── Check 3: remote server default gateway ────────────────
GW_RESULT=$(python3 "$DIR/ssh_helper.py" \
    "ip route show default | awk '/default/ {print \$3}' | head -1 | xargs -I{} ping -c 1 -W 3 {} 2>&1 | grep -q '1 received' && echo OK || echo FAIL" \
    2>/dev/null || echo "FAIL")
if [ "$GW_RESULT" = "OK" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" CHECK=\"gateway-reachable\" STATUS=\"PASS\"" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" CHECK=\"gateway-reachable\" STATUS=\"FAIL\" DETAIL=\"default gateway unreachable\"" >> "$LOG_FILE"
    echo "WARNING: Remote server default gateway is unreachable — possible upstream network issue" >&2
    FAILURES=$((FAILURES + 1))
fi

# ── Summary ────────────────────────────────────────────────
if [ "$FAILURES" -eq 0 ]; then
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" STATUS=\"SUCCESS\" DETAIL=\"all connectivity checks passed\"" >> "$LOG_FILE"
    echo "Network validation passed — all 3 checks OK on $REMOTE_IP."
else
    echo "[$TIMESTAMP] TRIGGER=\"validate_network\" STATUS=\"DEGRADED\" DETAIL=\"${FAILURES}/3 checks failed\"" >> "$LOG_FILE"
    echo "Network validation: ${FAILURES}/3 checks FAILED on $REMOTE_IP." >&2
    exit 1
fi
