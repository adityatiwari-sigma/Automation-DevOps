#!/bin/bash

# ============================================================
# UNIVERSAL RCA TRIAGE & RESOURCE ANALYSIS (AOPS)
# Searches ALL logs & Prometheus Metric API
# ============================================================

# Configuration
LOG_FILE="/home/adityatiwari/Documents/AOPS/auto-remediation.log"
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
# Searching for HTTP 5xx or keyword errors
QUERY="{platform=~\".+\"} |~ \"(?i)error|critical|fatal|oom|children|timeout|regenics|fail|refused| 500 | 502 | 504 \""
START_TIME=$(date -u -d '30 minutes ago' '+%Y-%m-%dT%H:%M:%SZ')

EVIDENCE=$(curl -s -G "$LOKI_URL" \
    --data-urlencode "query=$QUERY" \
    --data-urlencode "limit=50" \
    --data-urlencode "start=$START_TIME" | python3 -c "import sys, json; data=json.load(sys.stdin); [print(v[1]) for s in data['data']['result'] for v in s['values']]")

EVIDENCE_COUNT=$(echo "$EVIDENCE" | grep -v "^$" | wc -l)

# --- 3. The Decision Matrix (RCA Action) ---

# MATCH: HTTP Error Symptoms (The 500 search)
# Using grep -P for word boundaries to catch 500 precisely
if echo "$EVIDENCE" | grep -Eq " 500 | 502 | 504 |\"500\"|\"502\"|\"504\""; then
    MATCHING_LOG=$(echo "$EVIDENCE" | grep -E " 500 | 502 | 504 " | head -n 1)
    echo "[Confirmed] Root Cause: HTTP 5xx Symptom found. Log: $MATCHING_LOG" >> "$LOG_FILE"
    
    # Trigger the Evidence Capture for the Webhook
    echo "Evidence Found: $MATCHING_LOG"
    
    # FALLBACK REMEDIATION
    echo "Triggering PHP-FPM reload to clear upstream errors..." >> "$LOG_FILE"
    bash /home/adityatiwari/Documents/AOPS/remediate/fpm-reload.sh
    exit 0
fi

# MATCH: Specific Raw Errors
if echo "$EVIDENCE" | grep -Ei "too many open files|regenics_error|OOM killer|max_children"; then
    MATCHING_LOG=$(echo "$EVIDENCE" | grep -Ei "too many open files|regenics_error|OOM killer|max_children" | head -n 1)
    echo "[Confirmed] Critical System Error: $MATCHING_LOG" >> "$LOG_FILE"
    echo "Evidence Found: $MATCHING_LOG"
    
    bash /home/adityatiwari/Documents/AOPS/remediate/nginx-file-limit.sh
    exit 0
fi

# FALLBACK (When no patterns match)
TOP_SAMPLE=$(echo "$EVIDENCE" | head -n 1 | cut -c 1-100)
echo "[Skipped] No pattern found. Top sample: $TOP_SAMPLE" >> "$LOG_FILE"
# Send summary to webhook
echo "No evidence matching remediation patterns found in $EVIDENCE_COUNT logs."
