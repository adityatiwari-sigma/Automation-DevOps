#!/bin/bash

# ============================================================
# UNIVERSAL RCA TRIAGE & RESOURCE ANALYSIS (AOPS)
# Searches ALL logs & Prometheus Metric API
# ============================================================

# Configuration
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
LOKI_URL="http://localhost:3100/loki/api/v1/query_range"
PROM_URL="http://localhost:9090/api/v1/query"

# Labels from Webhook
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "--------------------------------------------------------" >> "$LOG_FILE"
echo "[$TIMESTAMP] 🔍 GLOBAL RCA INVESTIGATION STARTED..." >> "$LOG_FILE"

# --- 1. Prometheus Resource Snapshot (CPU & RAM) ---
CPU_USAGE=$(curl -s -G "$PROM_URL" --data-urlencode 'query=100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)' | python3 -c "import sys, json; print(round(float(json.load(sys.stdin)['data']['result'][0]['value'][1]), 2))" 2>/dev/null || echo "N/A")
MEM_USAGE=$(curl -s -G "$PROM_URL" --data-urlencode 'query=(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100' | python3 -c "import sys, json; print(round(float(json.load(sys.stdin)['data']['result'][0]['value'][1]), 2))" 2>/dev/null || echo "N/A")

echo "[Resource Photo] CPU: $CPU_USAGE% | RAM: $MEM_USAGE%" >> "$LOG_FILE"

# --- 2. Global Evidence Hunt (Loki) ---
QUERY="{platform=~\".+\"} |~ \"(?i)error|critical|fatal|oom|children|timeout|regenics|fail|refused| 500 | 502 | 504 \""
START_TIME=$(date -u -d '15 minutes ago' '+%Y-%m-%dT%H:%M:%SZ')

EVIDENCE=$(curl -s -G "$LOKI_URL" \
    --data-urlencode "query=$QUERY" \
    --data-urlencode "limit=50" \
    --data-urlencode "start=$START_TIME" | python3 -c "import sys, json; data=json.load(sys.stdin); [print(v[1]) for s in data['data']['result'] for v in s['values']]")

EVIDENCE_COUNT=$(echo "$EVIDENCE" | grep -v "^$" | wc -l)

# --- 3. The Decision Matrix (RCA Action) ---

# MATCH: Specific Raw Errors (Prioritized over generic 500s)
if echo "$EVIDENCE" | grep -Ei "max_children|pm.max_children"; then
    MATCHING_LOG=$(echo "$EVIDENCE" | grep -Ei "max_children|pm.max_children" | head -n 1)
    echo "[Confirmed] Root Cause: PHP-FPM Worker Exhaustion: $MATCHING_LOG" >> "$LOG_FILE"
    echo "Evidence Found: $MATCHING_LOG"
    bash "$DIR/fpm-reload.sh"
    exit 0

elif echo "$EVIDENCE" | grep -Ei "too many open files"; then
    MATCHING_LOG=$(echo "$EVIDENCE" | grep -Ei "too many open files" | head -n 1)
    echo "[Confirmed] Root Cause: Nginx File Descriptor Limit: $MATCHING_LOG" >> "$LOG_FILE"
    echo "Evidence Found: $MATCHING_LOG"
    bash "$DIR/nginx-file-limit.sh"
    exit 0

elif echo "$EVIDENCE" | grep -Ei "OOM command not allowed|OOM killer"; then
    MATCHING_LOG=$(echo "$EVIDENCE" | grep -Ei "OOM command not allowed|OOM killer" | head -n 1)
    echo "[Confirmed] Root Cause: Redis/System Memory Pressure: $MATCHING_LOG" >> "$LOG_FILE"
    echo "Evidence Found: $MATCHING_LOG"
    bash "$DIR/redis-flush-cache.sh"
    exit 0

elif echo "$EVIDENCE" | grep -Eq " 500 | 502 | 504 |\"500\"|\"502\"|\"504\""; then
    MATCHING_LOG=$(echo "$EVIDENCE" | grep -E " 500 | 502 | 504 " | head -n 1)
    echo "[Confirmed] Root Cause: Generic HTTP 5xx Symptom. Log: $MATCHING_LOG" >> "$LOG_FILE"
    echo "Evidence Found: $MATCHING_LOG"
    echo "Triggering PHP-FPM reload as first-line defense..." >> "$LOG_FILE"
    bash "$DIR/fpm-reload.sh"
    exit 0
fi

# FALLBACK (When no patterns match)
TOP_SAMPLE=$(echo "$EVIDENCE" | head -n 1 | cut -c 1-100)
echo "[Skipped] No pattern found. Top sample: $TOP_SAMPLE" >> "$LOG_FILE"
echo "No evidence matching remediation patterns found in $EVIDENCE_COUNT logs."
