#!/bin/bash

# Configuration
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
ENV_FILE="$(dirname "$DIR")/.env"

if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
fi

REMOTE_IP="${REMOTE_IP:-10.10.2.21}"
SSH_USER="${SSH_USER:-root}"
SSH_PASSWORD="${SSH_PASSWORD:-}"
SUDO_PASSWORD="${SUDO_PASSWORD:-}"

REDIS_HOST="${REMOTE_IP}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_CACHE_DB="${REDIS_CACHE_DB:-0}"
REDIS_SESSION_DB="${REDIS_SESSION_DB:-1}"

# 1. Exit 0 immediately if AUTO_REMEDIATE=false
if [ "${AUTO_REMEDIATE}" = "false" ]; then
    exit 0
fi

# 2. Hard-abort if $REDIS_CACHE_DB == $REDIS_SESSION_DB (safety guard)
if [ "$REDIS_CACHE_DB" = "$REDIS_SESSION_DB" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"redis-flush-cache\" ACTION=\"safety-check\" STATUS=\"FAILED\" REASON=\"Cache and Session DB are same\"" >> "$LOG_FILE"
    exit 1
fi

# 3. Connect to Redis and get memory info
REDIS_CLI="redis-cli -h $REDIS_HOST -p $REDIS_PORT"
MEM_INFO=$($REDIS_CLI INFO memory 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"redis-flush-cache\" ACTION=\"check-status\" STATUS=\"FAILED\" REASON=\"redis-cli failed for $REDIS_HOST\"" >> "$LOG_FILE"
    exit 1
fi

USED_MEMORY=$(echo "$MEM_INFO" | grep used_memory: | cut -d: -f2 | tr -d '\r')
MAX_MEMORY=$(echo "$MEM_INFO" | grep maxmemory: | cut -d: -f2 | tr -d '\r')

# If maxmemory is 0, compare against /proc/meminfo MemTotal on REMOTE
if [ -z "$MAX_MEMORY" ] || [ "$MAX_MEMORY" -eq 0 ]; then
    MAX_MEMORY=$(python3 "$DIR/ssh_helper.py" "$REMOTE_IP" "$SSH_USER" "$SSH_PASSWORD" "$SUDO_PASSWORD" "grep MemTotal /proc/meminfo" | awk '{print $2 * 1024}')
fi

if [ -z "$USED_MEMORY" ] || [ -z "$MAX_MEMORY" ] || [ "$MAX_MEMORY" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"redis-flush-cache\" ACTION=\"parse-mem\" STATUS=\"FAILED\"" >> "$LOG_FILE"
    exit 1
fi

RATIO=$(python3 -c "print($USED_MEMORY / $MAX_MEMORY)")

# 4. If ratio > 0.90, run redis-cli -n 0 FLUSHDB (cache DB only)
HIGH_MEMORY=$(python3 -c "print('true' if $RATIO > 0.90 else 'false')")

if [ "$HIGH_MEMORY" = "true" ]; then
    BEFORE_KEYS=$($REDIS_CLI -n "$REDIS_CACHE_DB" DBSIZE 2>/dev/null)
    
    if $REDIS_CLI -n "$REDIS_CACHE_DB" FLUSHDB >/dev/null 2>&1; then
        AFTER_KEYS=$($REDIS_CLI -n "$REDIS_CACHE_DB" DBSIZE 2>/dev/null)
        REMEDIATION_STATUS="SUCCESS"
        
        # Log key count before and after flush
        echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"redis-flush-cache\" ACTION=\"FLUSHDB-remote\" STATUS=\"SUCCESS\" TARGET=\"$REMOTE_IP\" BEFORE_KEYS=\"$BEFORE_KEYS\" AFTER_KEYS=\"$AFTER_KEYS\"" >> "$LOG_FILE"
        
        # 5. POST Slack message
        if [ -n "$SLACK_WEBHOOK_URL" ]; then
            PAYLOAD=$(cat <<EOF
{
  "text": "🧹 *Redis Cache Flushed (Remote: $REMOTE_IP)*",
  "attachments": [
    {
      "color": "good",
      "fields": [
        {"title": "Target IP", "value": "$REMOTE_IP", "short": true},
        {"title": "DB", "value": "$REDIS_CACHE_DB", "short": true},
        {"title": "Keys Before", "value": "$BEFORE_KEYS", "short": true},
        {"title": "Keys After", "value": "$AFTER_KEYS", "short": true},
        {"title": "Status", "value": "CONFIRMED: Session DB ($REDIS_SESSION_DB) Untouched", "short": false}
      ]
    }
  ]
}
EOF
)
            curl -s -X POST -H 'Content-type: application/json' --data "$PAYLOAD" "$SLACK_WEBHOOK_URL" > /dev/null
        fi
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"redis-flush-cache\" ACTION=\"FLUSHDB\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
    fi
fi
