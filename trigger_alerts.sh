#!/bin/bash
 
# ============================================================
# ALERT TRIGGER SCRIPT FOR AIOPS STACK
# This script simulates failures across Nginx, PHP, and MySQL
# to verify P1, P2, and P3 alert routing.
# ============================================================
 
# --- Configuration ---
NGINX_LOG="/var/log/nginx/access.log"
PHP_LOG="/var/log/php8.4-fpm.log"      
MYSQL_LOG="/var/log/mysql/mariadb-slow.log"      
 
# --- P1: Critical (Checkout/Payment Fatal) ---
trigger_p1() {
    echo "--- Triggering P1: Fatal Checkout Error ---"
    echo "127.0.0.1 - - [$(date '+%d/%b/%Y:%H:%M:%S %z')] \"POST /api/v1/payment/checkout HTTP/1.1\" 500 0 \"-\" \"-\" 5.001 [FATAL] Payment gateway failed" >> "$NGINX_LOG"
    echo "[DONE] P1 Alert should fire in < 1 min."
}
 
# --- P2: High (General System Fatals/Slow Queries) ---
trigger_p2() {
    echo "--- Triggering P2: General PHP Fatal (Non-Checkout) ---"
    for i in {1..12}; do
        # We use 'account.php' instead of 'checkout.php' to avoid P1 collision
        echo "[$(date '+%d-%b-%Y %H:%M:%S')] PHP Fatal error: Call to undefined function process_user() in /var/www/html/wp-content/plugins/core/account.php on line 10" >> "$PHP_LOG"
    done
    echo "[DONE] P2 Alert should fire in 1-2 mins."
}
 
# --- P3: Low (Warnings/Notices) ---
trigger_p3() {
    echo "--- Triggering P3: PHP Warnings/Notices ---"
    for i in {1..5}; do
        echo "[$(date '+%d-%b-%Y %H:%M:%S')] PHP Warning: disk_free_space(): Allowance exceeded in /var/www/html/index.php on line 5" >> "$PHP_LOG"
    done
    echo "[DONE] P3 Alert should fire (usually daily digest or low priority)."
}
 
# --- Main execution loop ---
case "$1" in
    p1) trigger_p1 ;;
    p2) trigger_p2 ;;
    p3) trigger_p3 ;;
    all)
        trigger_p1
        trigger_p2
        trigger_p3
        ;;
    *)
        echo "Usage: $0 {p1|p2|p3|all}"
        ;;
esac
