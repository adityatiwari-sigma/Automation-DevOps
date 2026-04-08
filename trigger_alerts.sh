#!/bin/bash
 
# ============================================================
# ALERT TRIGGER SCRIPT FOR AIOPS STACK
# This specifically generates logs that uniquely trigger
# 1-to-1 matching remediation scripts securely via SSH.
# ============================================================
 
# --- Configuration ---
NGINX_LOG="/var/log/nginx/error.log"
PHP_LOG="/var/log/php8.4-fpm.log"      
REDIS_LOG="/var/log/redis/redis-server.log"      
 
trigger_fpm() {
    echo "--- Triggering FPM Auto-Remediation (Specific) ---"
    echo "[$(date '+%d-%b-%Y %H:%M:%S')] WARNING: [pool www] server reached pm.max_children setting (5), consider raising it" >> "$PHP_LOG"
    echo "[DONE] FPM rule triggered."
}
 
trigger_nginx() {
    echo "--- Triggering Nginx Auto-Remediation (Specific) ---"
    echo "$(date '+%Y/%m/%d %H:%M:%S') [crit] 1234#0: *123 open() \"/var/www/html/index.php\" failed (24: too many open files) while logging request" >> "$NGINX_LOG"
    echo "[DONE] Nginx rule triggered."
}
 
trigger_redis() {
    echo "--- Triggering Redis Auto-Remediation (Specific) ---"
    echo "1234:M $(date '+%d %b %Y %H:%M:%S.000') # OOM command not allowed when used memory > 'maxmemory'." >> "$REDIS_LOG"
    echo "[DONE] Redis rule triggered."
}
 
# --- Main execution loop ---
case "$1" in
    fpm) trigger_fpm ;;
    nginx) trigger_nginx ;;
    redis) trigger_redis ;;
    all)
        trigger_fpm
        trigger_nginx
        trigger_redis
        ;;
    *)
        echo "Usage: $0 {fpm|nginx|redis|all}"
        ;;
esac
