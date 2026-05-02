#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# REDIS CACHE FLUSH — production-safe cache-only FLUSHDB
# Safety guards:
#   1. Aborts if AUTO_REMEDIATE=false
#   2. Aborts if cache DB == session DB
#   3. Runs FLUSHDB only when used/max memory ratio > 0.90
# ============================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
CONFIG_FILE="$(dirname "$DIR")/config.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Read config ────────────────────────────────────────────
read_cfg() {
    python3 -c "import json, sys
cfg = json.load(open('$CONFIG_FILE'))
keys = '$1'.split('.')
v = cfg
for k in keys:
    v = v[k]
print(v)"
}

AUTO_REMEDIATE=$(read_cfg "webhook.auto_remediate" 2>/dev/null || echo "True")
REMOTE_IP=$(read_cfg "network.remote_ip")
REDIS_PORT=$(read_cfg "redis.port")
REDIS_CACHE_DB=$(read_cfg "redis.cache_db")
REDIS_SESSION_DB=$(read_cfg "redis.session_db")
REDIS_PASSWORD=$(read_cfg "redis.password" 2>/dev/null || echo "")
SLACK_URL=$(read_cfg "slack.webhook_url" 2>/dev/null || echo "")

# ── Guard 1: AUTO_REMEDIATE ────────────────────────────────
if [ "$AUTO_REMEDIATE" = "False" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"redis-flush-cache\" STATUS=\"SKIPPED\" REASON=\"auto_remediate=false\"" >> "$LOG_FILE"
    exit 0
fi

# ── Guard 2: cache DB ≠ session DB ────────────────────────
if [ "$REDIS_CACHE_DB" = "$REDIS_SESSION_DB" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"redis-flush-cache\" STATUS=\"ABORTED\" REASON=\"cache_db==session_db safety guard\"" >> "$LOG_FILE"
    echo "ERROR: cache_db and session_db are the same — refusing to flush." >&2
    exit 1
fi

# ── Build redis-cli command ────────────────────────────────
REDIS_CLI_BASE="redis-cli -h $REMOTE_IP -p $REDIS_PORT"
if [ -n "$REDIS_PASSWORD" ]; then
    REDIS_CLI_BASE="$REDIS_CLI_BASE -a $REDIS_PASSWORD"
fi

# ── Guard 3: memory threshold check ───────────────────────
MEM_INFO=$($REDIS_CLI_BASE INFO memory 2>/dev/null) || {
    echo "[$TIMESTAMP] TRIGGER=\"redis-flush-cache\" STATUS=\"FAILED\" REASON=\"redis-cli unreachable\"" >> "$LOG_FILE"
    exit 1
}

USED_MEMORY=$(echo "$MEM_INFO" | grep '^used_memory:' | cut -d: -f2 | tr -d '\r ')
MAX_MEMORY=$(echo "$MEM_INFO"  | grep '^maxmemory:' | cut -d: -f2 | tr -d '\r ')

if [ -z "$MAX_MEMORY" ] || [ "$MAX_MEMORY" -eq 0 ]; then
    MAX_MEMORY=$(python3 "$DIR/ssh_helper.py" "grep MemTotal /proc/meminfo | awk '{print \$2 * 1024}'" 2>/dev/null || echo "0")
fi

if [ -z "$USED_MEMORY" ] || [ -z "$MAX_MEMORY" ] || [ "$MAX_MEMORY" -eq 0 ]; then
    echo "[$TIMESTAMP] TRIGGER=\"redis-flush-cache\" STATUS=\"FAILED\" REASON=\"could not determine memory usage\"" >> "$LOG_FILE"
    exit 1
fi

HIGH_MEM=$(python3 -c "print('true' if $USED_MEMORY / $MAX_MEMORY > 0.90 else 'false')")

if [ "$HIGH_MEM" = "true" ]; then
    BEFORE_KEYS=$($REDIS_CLI_BASE -n "$REDIS_CACHE_DB" DBSIZE 2>/dev/null || echo "unknown")

    if $REDIS_CLI_BASE -n "$REDIS_CACHE_DB" FLUSHDB >/dev/null 2>&1; then
        AFTER_KEYS=$($REDIS_CLI_BASE -n "$REDIS_CACHE_DB" DBSIZE 2>/dev/null || echo "0")
        echo "[$TIMESTAMP] TRIGGER=\"redis-flush-cache\" ACTION=\"FLUSHDB\" STATUS=\"SUCCESS\" TARGET=\"$REMOTE_IP\" DB=\"$REDIS_CACHE_DB\" BEFORE_KEYS=\"$BEFORE_KEYS\" AFTER_KEYS=\"$AFTER_KEYS\"" >> "$LOG_FILE"

        # Optional Slack notification
        if [ -n "$SLACK_URL" ]; then
            curl -s -X POST -H 'Content-type: application/json' --data \
                "{\"text\":\"Redis cache flushed on $REMOTE_IP — DB $REDIS_CACHE_DB — $BEFORE_KEYS keys removed, session DB $REDIS_SESSION_DB untouched\"}" \
                "$SLACK_URL" > /dev/null
        fi
    else
        echo "[$TIMESTAMP] TRIGGER=\"redis-flush-cache\" ACTION=\"FLUSHDB\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
        exit 1
    fi
else
    echo "[$TIMESTAMP] TRIGGER=\"redis-flush-cache\" STATUS=\"SKIPPED\" REASON=\"memory ratio below 90% threshold\"" >> "$LOG_FILE"
fi
