#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# UNIVERSAL RCA TRIAGE — queries all signals, routes to the
# right specific remediation script
# ============================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
CONFIG_FILE="$(dirname "$DIR")/config.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Read configured host IPs from config.json ─────────────
LOCAL_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['network']['local_ip'])")
PROM_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['ports']['prometheus'])")
LOKI_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['ports']['loki'])")

PROM_URL="http://${LOCAL_IP}:${PROM_PORT}/api/v1/query"
LOKI_URL="http://${LOCAL_IP}:${LOKI_PORT}/loki/api/v1/query_range"

echo "--------------------------------------------------------" >> "$LOG_FILE"
echo "[$TIMESTAMP] RCA INVESTIGATION STARTED" >> "$LOG_FILE"

# ── 1. Prometheus resource snapshot ───────────────────────
CPU_USAGE=$(curl -sf -G "$PROM_URL" \
    --data-urlencode 'query=100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)' \
    | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(round(float(r[0]['value'][1]),1)) if r else print('N/A')" \
    2>/dev/null || echo "N/A")

MEM_USAGE=$(curl -sf -G "$PROM_URL" \
    --data-urlencode 'query=(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100' \
    | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(round(float(r[0]['value'][1]),1)) if r else print('N/A')" \
    2>/dev/null || echo "N/A")

echo "[$TIMESTAMP] Resource: CPU=${CPU_USAGE}% MEM=${MEM_USAGE}%" >> "$LOG_FILE"

# ── 2. Loki evidence hunt (last 15 minutes) ───────────────
QUERY='{platform=~".+"} |~ "(?i)error|critical|fatal|oom|max_children|timeout|fail|refused| 500 | 502 | 504 "'
START_TIME=$(date -u -d '15 minutes ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null \
    || python3 -c "from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) - timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ'))")

EVIDENCE=$(curl -sf -G "$LOKI_URL" \
    --data-urlencode "query=$QUERY" \
    --data-urlencode "limit=50" \
    --data-urlencode "start=$START_TIME" \
    | python3 -c "import sys,json; data=json.load(sys.stdin); [print(v[1]) for s in data['data']['result'] for v in s['values']]" \
    2>/dev/null || echo "")

# ── 3. Decision matrix ────────────────────────────────────
if echo "$EVIDENCE" | grep -qEi "max_children|pm\.max_children"; then
    MATCH=$(echo "$EVIDENCE" | grep -Ei "max_children|pm\.max_children" | head -n 1)
    echo "[$TIMESTAMP] [Confirmed] Root Cause: PHP-FPM Worker Exhaustion" >> "$LOG_FILE"
    echo "Confirmed: PHP-FPM Worker Exhaustion. Evidence: $MATCH"
    bash "$DIR/fpm-reload.sh"

elif echo "$EVIDENCE" | grep -qEi "too many open files"; then
    MATCH=$(echo "$EVIDENCE" | grep -Ei "too many open files" | head -n 1)
    echo "[$TIMESTAMP] [Confirmed] Root Cause: Nginx File Descriptor Limit" >> "$LOG_FILE"
    echo "Confirmed: Nginx File Descriptor Limit. Evidence: $MATCH"
    bash "$DIR/nginx-file-limit.sh"

elif echo "$EVIDENCE" | grep -qEi "OOM command not allowed|OOM killer"; then
    MATCH=$(echo "$EVIDENCE" | grep -Ei "OOM command not allowed|OOM killer" | head -n 1)
    echo "[$TIMESTAMP] [Confirmed] Root Cause: Redis/System Memory Pressure" >> "$LOG_FILE"
    echo "Confirmed: Redis Memory Pressure. Evidence: $MATCH"
    bash "$DIR/redis-flush-cache.sh"

elif echo "$EVIDENCE" | grep -qE ' (500|502|504) |"(500|502|504)"'; then
    MATCH=$(echo "$EVIDENCE" | grep -E ' (500|502|504) ' | head -n 1)
    echo "[$TIMESTAMP] [Confirmed] Root Cause: Generic HTTP 5xx — triggering PHP-FPM reload as first defence" >> "$LOG_FILE"
    echo "Confirmed: HTTP 5xx errors. Evidence: $MATCH"
    bash "$DIR/fpm-reload.sh"

else
    SAMPLE=$(echo "$EVIDENCE" | head -n 1 | cut -c 1-120)
    echo "[$TIMESTAMP] [Skipped] No pattern matched. Sample: $SAMPLE" >> "$LOG_FILE"
    echo "No matching remediation pattern found in Loki evidence."
fi
