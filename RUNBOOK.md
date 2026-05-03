# Operational Runbook — AIOps Platform

> This document is for on-call engineers. Open it when an alert fires. Every section ends with an actionable command or decision. For architecture and setup detail, see `README.md`.

**Platform:** dev.regenics.com | **Remote Server:** <remote_ip> | **Monitoring Laptop:** 10.10.2.77
**Webhook Service:** http://10.10.2.77:5051 | **Grafana:** http://10.10.2.77:3000 | **Prometheus:** http://10.10.2.77:9090

---

## Quick Reference: Alert → Action Matrix

| Alert Name | Severity | Source | Auto-Remediated? | Script | First Response | Escalate If |
|---|---|---|---|---|---|---|
| P1_5xxErrorRateCritical | P1 | Prometheus | Yes (generic_triage) | `generic_triage.sh` | Check Grafana error dashboard | Persists > 5 min after remediation |
| P1_5xxCountSpike | P1 | Prometheus | Yes (generic_triage) | `generic_triage.sh` | Check `auto-remediation.log` | > 50 errors/2min persists |
| PHPFPMWorkerPoolExhaustion | P1 | Prometheus | Yes (generic_triage) | → `fpm-reload.sh` | Verify FPM reload completed | Workers still exhausted |
| HighHTTP5xxRate | P1 | Prometheus | Yes (generic_triage) | `generic_triage.sh` | Await remediation result | Error rate not dropping |
| FPM_WorkerPoolExhaustion | P1 | Loki | Yes (fpm-reload) | `fpm-reload.sh` | Check FPM status on server | Reload failed, manual restart needed |
| Nginx_TooManyOpenFiles | P1 | Loki | Yes (nginx-file-limit) | `nginx-file-limit.sh` | Verify sysctl applied | Nginx still erroring |
| Redis_Memory_High | P1 | Loki | Yes (redis-flush-cache) | `redis-flush-cache.sh` | Check cache flush completed | Redis still OOM |
| P1_CheckoutPaymentFatal | P1 | Loki | Yes (generic_triage) | `generic_triage.sh` | Check checkout logs immediately | Any payment fatal → escalate always |
| LcpDegraded_CpuBottleneck | P1 | Prometheus | No | — | Check CPU hogs, scale resources | CPU sustained > 30 min |
| P2_HighRequestLatency | P2 | Prometheus | No | — | Check slow URL breakdown | P95 > 8000ms or growing |
| P2_SlowURLLatency | P2 | Prometheus | No | — | Identify specific slow endpoint | Revenue-critical URL affected |
| P2_UpstreamConnectionErrors | P2 | Prometheus | No | — | Check PHP-FPM and backend | 502/503/504 rate rising |
| P2_AppFatalRateHigh | P2 | Loki | Yes (generic_triage) | `generic_triage.sh` | Check error log patterns | Fatal rate growing or checkout affected |
| P2_MySQLIssuesHigh | P2 | Loki | Yes (generic_triage) | `generic_triage.sh` | Check slow query dashboard | Table locks, disk full |
| LcpDegraded_ServerHealthy | P2 | Prometheus | No | — | Check recent git deployments | LCP > 6000ms |
| TargetDown | P2 | Prometheus | No | — | Check exporter service on remote | Host unreachable |
| AlertmanagerNotificationFailing | P2 | Prometheus | No | — | Check SMTP config, test email | All notifications silenced |
| P3_DiskSpaceLow | P3 | Prometheus | No | — | Review disk usage, clean old files | Below 10% or growing |
| P3_HighMemoryUsage | P3 | Prometheus | No | — | Review process memory, check swapping | OOM kill events occurring |
| P3_HighCPUUsage | P3 | Prometheus | No | — | Identify top processes | Sustained > 24h |
| P3_PlatformWarningRateElevated | P3 | Loki | No | — | Review warning log stream | Warnings converting to errors |

---

## On-Call Escalation Path

```
Level 1 — First responder (you, reading this now)
  Action: Follow the procedure for the specific alert below.
  Time budget: 15 minutes for P1, 1 hour for P2.

Level 2 — Platform Owner
  Contact: Aditya Tiwari (Sigma Informatics DevOps)
  Escalate when: Auto-remediation failed twice, or a P1 involves payment/checkout,
                 or the monitoring stack itself is down.

Level 3 — Client Contact
  Escalate when: Service is down and P1 cannot be resolved in 30 minutes,
                 or data integrity concern exists (DB corruption, Redis session loss).

Maintenance window rule: If you must disable auto-remediation, set
  "auto_remediate": false in config.json, run generate_configs.py,
  restart the webhook service, and notify the team.
```

---

## Pre-Flight: How to Access Systems

### Monitoring Laptop Services

```bash
# Grafana — dashboards and alert history
http://10.10.2.77:3000
# Username: admin / Password: (see config.json → grafana.admin_password)

# Prometheus — raw metrics and rule evaluation
http://10.10.2.77:9090

# Alertmanager — active alerts and silences
http://10.10.2.77:9093

# Webhook service status page — last 20 remediation log lines
http://10.10.2.77:5051

# Webhook health check
curl http://10.10.2.77:5051/health
```

### Remote Server (<remote_ip>)

```bash
# SSH access (use credentials from config.json → ssh section)
ssh <ssh_user>@<remote_ip>

# Verify services are running
sudo systemctl status nginx
sudo systemctl status php8.4-fpm
sudo systemctl status mysql
sudo systemctl status redis-server
sudo systemctl status node_exporter
sudo systemctl status promtail
```

### Docker Stack (on monitoring laptop)

```bash
# View all container statuses
docker compose -f /path/to/Automation-DevOps/docker-compose.yml ps

# Shortcut if you are in the project directory
cd /path/to/Automation-DevOps && docker compose ps
```

### Credentials Location

All credentials are stored in `/path/to/Automation-DevOps/config.json` on the monitoring laptop. This file is **not** committed to git. If you do not have access, contact the platform owner.

---

## P1 CRITICAL Alert Procedures

P1 alerts have a 15-minute repeat interval. Auto-remediated P1 alerts also trigger the webhook before the email is sent. **Check `auto-remediation.log` first** — remediation may already be in progress.

```bash
# Always start here for any P1:
tail -50 /path/to/Automation-DevOps/auto-remediation.log
```

---

### P1_5xxErrorRateCritical

**What it means:** More than 5% of HTTP requests have returned 5xx status codes for at least 2 consecutive minutes. Users are receiving errors.

**What auto-remediation does:** `generic_triage.sh` is dispatched. It queries Prometheus (CPU/memory) and Loki (recent log patterns) to identify the specific root cause, then calls the appropriate specific script (`fpm-reload.sh`, `nginx-file-limit.sh`, or `redis-flush-cache.sh`).

**Procedure:**

```bash
# 1. Check if remediation ran
tail -30 /path/to/Automation-DevOps/auto-remediation.log

# 2. Check current 5xx rate in Prometheus
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=sum(rate(nginx_http_requests_total{status=~"5.."}[2m])) / sum(rate(nginx_http_requests_total[2m])) * 100' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 'no data')"

# 3. Open Grafana error dashboard
# http://10.10.2.77:3000/d/error-groups-v2

# 4. SSH to remote server and check Nginx error log
ssh user@<remote_ip>
sudo tail -100 /var/log/nginx/error.log | grep -E "crit|error|emerg"

# 5. Check PHP-FPM status
sudo systemctl status php8.4-fpm
```

**If auto-remediation fails:** See Manual Recovery → Restart PHP-FPM section.

**Escalate if:** Error rate does not drop below 2% within 10 minutes of remediation, or if the errors are on the checkout/payment flow (treat as P1_CheckoutPaymentFatal).

---

### P1_5xxCountSpike

**What it means:** More than 50 HTTP 5xx errors occurred within a 2-minute window. An absolute count threshold — designed to catch sudden bursts even if the rate percentage is below 5%.

**What auto-remediation does:** Same as above — `generic_triage.sh` dispatched.

**Procedure:**

```bash
# 1. Check absolute 5xx count
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=sum(increase(nginx_http_requests_total{status=~"5.."}[2m]))' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 'no data')"

# 2. Check which status codes are spiking
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=sum by (status)(increase(nginx_http_requests_total{status=~"5.."}[5m]))' \
  | python3 -m json.tool

# 3. Check nginx access log for affected URLs
ssh user@<remote_ip>
sudo grep -E '" (500|502|503|504) ' /var/log/nginx/access.log | tail -30
```

**Escalate if:** Count is growing and not recovering, or error pattern is tied to a specific URL that processes payments.

---

### PHPFPMWorkerPoolExhaustion (Prometheus metric rule)

**What it means:** PHP-FPM active processes exceed 90% of `pm.max_children`. New requests are queueing. If this reaches 100%, Nginx returns 502.

**What auto-remediation does:** `generic_triage.sh` → `fpm-reload.sh` → SSH → `systemctl reload php8.4-fpm`. A graceful reload drains in-progress requests and starts fresh workers.

**Procedure:**

```bash
# 1. Check if reload ran and succeeded
grep "fpm-reload" /path/to/Automation-DevOps/auto-remediation.log | tail -10

# 2. Check current FPM pool utilization
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=phpfpm_active_processes{pool="www"} / phpfpm_max_active_processes{pool="www"} * 100' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 'no data')"

# 3. Check FPM status on remote server
ssh user@<remote_ip>
sudo systemctl status php8.4-fpm
# Check for slow scripts consuming workers:
sudo grep "request duration" /var/log/php8.4-fpm.log | sort -t'=' -k2 -rn | head -10

# 4. If reload did not resolve exhaustion — check pm.max_children setting
sudo cat /etc/php/8.4/fpm/pool.d/www.conf | grep -E "pm\.(max_children|start_servers|min_spare|max_spare)"
```

**If auto-remediation fails or keeps re-firing:**

```bash
# Manual FPM restart (harder than reload — kills in-flight requests)
ssh user@<remote_ip>
sudo systemctl restart php8.4-fpm

# Identify runaway PHP processes consuming workers
sudo ps aux | grep php | awk '{print $2, $11, $12}' | head -20
```

**Escalate if:** Pool is repeatedly exhausted after reload, or active process count is not decreasing. May require increasing `pm.max_children` in `/etc/php/8.4/fpm/pool.d/www.conf`.

---

### HighHTTP5xxRate (Prometheus metric rule, auto-remediation group)

**What it means:** Same signal as P1_5xxErrorRateCritical but defined in the auto-remediation rule group specifically to dispatch `generic_triage.sh`. These two rules cover the same underlying condition from different angles.

**Procedure:** Follow the same steps as P1_5xxErrorRateCritical above.

---

### FPM_WorkerPoolExhaustion (Loki log rule)

**What it means:** Promtail has detected the string `server reached pm.max_children` in the PHP-FPM error log within the last 1 minute. This is a log-signal version of the metric-based PHPFPMWorkerPoolExhaustion alert.

**What auto-remediation does:** `fpm-reload.sh` is dispatched directly (no triage step needed — the log pattern is unambiguous). SSH → `systemctl reload php8.4-fpm`.

**Procedure:**

```bash
# 1. Verify the log event in Loki (LogQL)
# In Grafana → Explore → Loki datasource:
# {platform="php-fpm"} |~ "max_children"

# 2. Check FPM reload result
grep "fpm-reload" /path/to/Automation-DevOps/auto-remediation.log | tail -5

# 3. Verify FPM is running
ssh user@<remote_ip>
sudo systemctl status php8.4-fpm
```

**If auto-remediation fails:**

```bash
ssh user@<remote_ip>
sudo systemctl reload php8.4-fpm
# If reload fails:
sudo systemctl restart php8.4-fpm
sudo journalctl -u php8.4-fpm -n 50
```

---

### Nginx_TooManyOpenFiles (Loki log rule)

**What it means:** Nginx error log contains `too many open files`. The OS file descriptor limit has been hit. Nginx cannot open new connections or files.

**What auto-remediation does:** `nginx-file-limit.sh` → SSH → `sysctl -w fs.file-max=100000` + `systemctl reload nginx`.

**Procedure:**

```bash
# 1. Check remediation log
grep "nginx-file-limit" /path/to/Automation-DevOps/auto-remediation.log | tail -5

# 2. Verify sysctl applied on remote server
ssh user@<remote_ip>
sysctl fs.file-max
# Expected: fs.file-max = 100000

# 3. Check current open file count
sudo lsof | wc -l
sudo cat /proc/sys/fs/file-nr

# 4. Check Nginx status
sudo systemctl status nginx
```

**If auto-remediation fails:**

```bash
ssh user@<remote_ip>
sudo sysctl -w fs.file-max=100000
sudo sysctl -w net.core.somaxconn=65535
# Make permanent:
echo "fs.file-max = 100000" | sudo tee -a /etc/sysctl.conf
sudo systemctl reload nginx
```

**Escalate if:** File count is still climbing after sysctl change. May indicate a file handle leak in the application.

---

### Redis_Memory_High (Loki log rule)

**What it means:** Redis log contains `OOM command not allowed` or `OOM killer`. Redis has hit its memory limit and is refusing write commands.

**What auto-remediation does:** `redis-flush-cache.sh` with three safety guards: (1) checks `auto_remediate` flag, (2) verifies cache_db ≠ session_db, (3) only flushes if memory ratio > 90%. Flushes only the cache DB (DB0 by default), never the session DB.

**Procedure:**

```bash
# 1. Check remediation log
grep "redis-flush-cache" /path/to/Automation-DevOps/auto-remediation.log | tail -5

# 2. Check Redis memory status
redis-cli -h <remote_ip> info memory | grep -E "used_memory_human|maxmemory_human|used_memory_peak_human"

# 3. Check cache DB key count
redis-cli -h <remote_ip> -n 0 DBSIZE

# 4. Check session DB is untouched
redis-cli -h <remote_ip> -n 1 DBSIZE
```

**If auto-remediation fails (safety guard triggered):**

```bash
# Check if cache_db == session_db (guard condition)
python3 -c "import json; c=json.load(open('config.json')); print('SAME' if c['redis']['cache_db']==c['redis']['session_db'] else 'DIFFERENT')"

# Manual safe flush — only after confirming DB indexes
redis-cli -h <remote_ip> -n 0 FLUSHDB  # flushes cache DB only
# Do NOT run FLUSHALL — this destroys session data
```

**Escalate if:** Sessions are being lost (DB1 was accidentally flushed), or memory stays high after flush (possible memory leak in Redis).

---

### P1_CheckoutPaymentFatal

**What it means:** A FATAL, EMERGENCY, or CRITICAL error has been detected in logs on a URL path matching checkout, payment, cart, order, billing, or invoice. **This alert always warrants immediate human review regardless of auto-remediation status.**

**What auto-remediation does:** `generic_triage.sh` is dispatched, but this alert must be reviewed by a human immediately. A fatal error in the payment flow may indicate data integrity issues.

**Procedure:**

```bash
# 1. IMMEDIATELY open error log stream in Grafana
# http://10.10.2.77:3000/d/error-groups-v2
# Filter by platform: php-fpm or laravel, filter by /checkout or /payment

# 2. Check Loki directly (LogQL)
# In Grafana Explore → Loki:
# {platform=~"php-fpm|laravel"} |~ "(?i)(fatal|emergency|critical)" |~ "(?i)(checkout|payment)"

# 3. Check PHP error log on remote server
ssh user@<remote_ip>
sudo tail -200 /var/log/php8.4-fpm.log | grep -Ei "fatal|critical" | tail -30
sudo grep -r "FATAL\|CRITICAL" /var/www/*/storage/logs/ 2>/dev/null | tail -30

# 4. Check database for incomplete transactions
# (Connect to MySQL and check recent orders table for incomplete/error states)

# 5. Check auto-remediation result
tail -30 /path/to/Automation-DevOps/auto-remediation.log
```

**Escalate immediately if:** Any payment transaction may have been affected, database shows incomplete records, or the error recurs after remediation.

---

### LcpDegraded_CpuBottleneck

**What it means:** LCP (Largest Contentful Paint) exceeds 4000ms AND server CPU usage is above 85%. The server is compute-saturated, causing slow page renders.

**What auto-remediation does:** Nothing — this alert is observational. It fires to notify the engineer that the root cause of LCP degradation has been diagnosed as CPU saturation.

**Procedure:**

```bash
# 1. Check CPU usage now
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=100 - (avg(rate(node_cpu_seconds_total{mode="idle",instance_name="dev-regenics"}[5m])) * 100)' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 'no data')"

# 2. Identify CPU-heavy processes on remote server
ssh user@<remote_ip>
top -bn1 | head -20
ps aux --sort=-%cpu | head -15

# 3. Check for runaway PHP worker processes
sudo ps aux | grep php-fpm | awk '{print $2, $3, $11}' | sort -k2 -rn | head -10

# 4. Check if a recent deployment happened
sudo journalctl --since "30 minutes ago" | grep -i "deploy\|restart\|reload"
# Check application deployment logs:
ls -lt /var/www/*/releases/ 2>/dev/null | head -5

# 5. Open LCP Correlation dashboard
# http://10.10.2.77:3000/d/lcp-correlation
```

**Escalate if:** CPU stays above 85% for more than 30 minutes, or if process count is growing (possible process leak).

---

## P2 HIGH Alert Procedures

P2 alerts have a 1-hour repeat interval. Respond within 1 hour. Check auto-remediation log first if the alert has a `remediation` label.

---

### P2_HighRequestLatency

**What it means:** The 95th percentile request latency has exceeded 4500ms for 5 consecutive minutes. Users in the slowest 5% are experiencing requests taking more than 4.5 seconds.

**Procedure:**

```bash
# 1. Check current P95 latency
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=histogram_quantile(0.95, sum(rate(promtail_custom_nginx_request_duration_ms_bucket[5m])) by (le))' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 'no data')"

# 2. Check which URLs are slowest (P2_SlowURLLatency may also be firing)
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=topk(10, histogram_quantile(0.95, sum by (le, uri)(rate(promtail_custom_nginx_request_duration_ms_bucket[5m]))))' \
  | python3 -m json.tool

# 3. Check PHP slow log
ssh user@<remote_ip>
sudo grep "request duration" /var/log/php8.4-fpm.log | sort -t'=' -k2 -rn | head -20

# 4. Check MySQL slow query log
sudo tail -100 /var/log/mysql/mysql-slow.log 2>/dev/null || \
  sudo grep "Query_time" /var/log/mysql/error.log | tail -20
```

---

### P2_SlowURLLatency

**What it means:** A specific URL endpoint has P95 latency above 4500ms for 5 minutes. The alert label `$labels.uri` contains the specific path.

**Procedure:**

```bash
# 1. Identify the slow URL from the alert labels (check email or Alertmanager UI)
# http://10.10.2.77:9093

# 2. Check Nginx access log for that URL
ssh user@<remote_ip>
sudo grep "POST /checkout\|GET /product" /var/log/nginx/access.log | \
  awk '{print $NF, $7}' | sort -rn | head -20

# 3. Check for slow PHP execution on that endpoint
sudo grep -A5 "slow log" /var/log/php8.4-fpm.log | grep -i "script_filename\|/checkout" | head -10

# 4. If MySQL-related endpoint, check for missing index
# EXPLAIN SELECT ... in MySQL for the suspected query
```

---

### P2_UpstreamConnectionErrors

**What it means:** 502 (Bad Gateway), 503 (Service Unavailable), or 504 (Gateway Timeout) errors from Nginx at a rate greater than 0.5 per second for 5 minutes. Nginx cannot reach PHP-FPM.

**Procedure:**

```bash
# 1. Check 502/503/504 rate
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=sum by (status)(rate(nginx_http_requests_total{status=~"502|503|504"}[5m]))' \
  | python3 -m json.tool

# 2. Check PHP-FPM socket/TCP connection
ssh user@<remote_ip>
sudo systemctl status php8.4-fpm
# Check PHP-FPM socket exists
ls -la /run/php/php8.4-fpm.sock 2>/dev/null || echo "Socket not found"

# 3. Check Nginx upstream config
sudo nginx -t
sudo grep -r "fastcgi_pass\|proxy_pass" /etc/nginx/sites-enabled/ | head -10

# 4. Check PHP-FPM listen directive
sudo grep "listen" /etc/php/8.4/fpm/pool.d/www.conf | head -5
```

---

### P2_AppFatalRateHigh

**What it means:** PHP fatal errors, parse errors, or "allowed memory size exhausted" errors are appearing in logs on a non-checkout path.

**What auto-remediation does:** `generic_triage.sh` is dispatched.

**Procedure:**

```bash
# 1. Check auto-remediation result
tail -20 /path/to/Automation-DevOps/auto-remediation.log

# 2. Find the specific fatal errors in Loki
# Grafana Explore → Loki:
# {platform=~"php-fpm|laravel"} |~ "(?i)(php fatal|parse error|memory size exhausted)"

# 3. Check PHP error log on remote server
ssh user@<remote_ip>
sudo tail -200 /var/log/php8.4-fpm.log | grep -Ei "fatal error|parse error|memory size" | tail -20

# 4. Check Laravel/WordPress application logs
sudo find /var/www -name "*.log" -newer /var/log/syslog | head -10
sudo tail -100 /var/www/*/storage/logs/laravel.log 2>/dev/null | grep -Ei "fatal|error" | tail -20
```

---

### P2_MySQLIssuesHigh

**What it means:** MySQL logs contain errors, slow queries, lock time events, or "table is full" messages.

**What auto-remediation does:** `generic_triage.sh` is dispatched (queries Loki for more detail).

**Procedure:**

```bash
# 1. Check auto-remediation log
grep "generic_triage\|mysql" /path/to/Automation-DevOps/auto-remediation.log | tail -10

# 2. Check MySQL status on remote server
ssh user@<remote_ip>
sudo mysqladmin status
sudo mysqladmin processlist | head -20

# 3. Check for table locks
sudo mysql -e "SHOW ENGINE INNODB STATUS\G" 2>/dev/null | grep -A20 "LATEST DETECTED DEADLOCK"
sudo mysql -e "SHOW PROCESSLIST;" | grep -v Sleep | head -20

# 4. Check slow query log
sudo tail -100 /var/log/mysql/mysql-slow.log 2>/dev/null | head -50
# Or via Grafana LCP Correlation dashboard slow query panel:
# http://10.10.2.77:3000/d/lcp-correlation
```

---

### LcpDegraded_ServerHealthy

**What it means:** LCP exceeds 4000ms but server CPU is below 85%. This points to an application-level issue — most likely a recent code deployment or an external dependency (CDN, third-party scripts, APIs).

**Procedure:**

```bash
# 1. Check recent deployments
ssh user@<remote_ip>
sudo journalctl --since "2 hours ago" | grep -i "deploy\|git\|composer\|npm"
ls -lt /var/www/*/releases/ 2>/dev/null | head -10

# 2. Check external API response times (if applicable)
# Review application logs for external HTTP call durations
sudo grep -r "curl\|guzzle\|http_client" /var/www/*/storage/logs/ 2>/dev/null | grep -i "timeout\|error" | tail -20

# 3. Check CDN configuration (DNS TTL, cache purge needed?)

# 4. Check if third-party scripts are blocking render
# Open Grafana Playwright dashboard for probe details:
# http://10.10.2.77:3000/d/playwright-probing

# 5. Manually run a Playwright probe to get fresh CWV data
# On remote server, check crontab for probe command:
crontab -l | grep playwright
```

---

### TargetDown

**What it means:** Prometheus cannot reach a scrape target (job/instance shown in alert). The exporter process may be stopped, the firewall may be blocking the port, or the host may be down.

**Procedure:**

```bash
# 1. Identify which target is down from the alert (check email Subject or Alertmanager UI)
# http://10.10.2.77:9093
# http://10.10.2.77:9090/targets  — look for red targets

# 2a. If remote server exporter is down (node_exporter :9100 or nginx :9113 or promtail :9080)
ssh user@<remote_ip>
sudo systemctl status node_exporter
sudo systemctl status nginx-prometheus-exporter 2>/dev/null || sudo systemctl status nginx-exporter
sudo systemctl status promtail

# Restart if stopped:
sudo systemctl start node_exporter
sudo systemctl start promtail

# 2b. Test port reachability from monitoring laptop
nc -zv <remote_ip> 9100  # node exporter
nc -zv <remote_ip> 9113  # nginx exporter
nc -zv <remote_ip> 9080  # promtail

# 3. If Docker container exporter is down (cadvisor :8080 or local node-exporter :9100)
cd /path/to/Automation-DevOps && docker compose ps
docker compose restart node-exporter  # or cadvisor
```

---

### AlertmanagerNotificationFailing

**What it means:** Alertmanager has been failing to send notifications (email or webhook) for 5 minutes. This means alerts may be firing silently.

**Procedure:**

```bash
# 1. Check Alertmanager logs for specific error
docker logs alertmanager --tail 50 | grep -i "error\|fail\|smtp"

# 2. Test email manually
cd /path/to/Automation-DevOps && python3 test_email.py

# 3. Check Gmail App Password validity
# Gmail App Passwords expire when Google Account 2FA changes.
# Regenerate at: https://myaccount.google.com/apppasswords
# Update config.json → email.app_password → run generate_configs.py → reload Alertmanager

# 4. Reload Alertmanager after config fix
curl -X POST http://localhost:9093/-/reload

# 5. Check webhook endpoint
curl -s http://localhost:5051/health
# If webhook is down: sudo systemctl restart auto-remediation-webhook
```

---

## P3 LOW Alert Procedures

P3 alerts are batched into a 24-hour digest. They indicate trends that need attention but are not immediately breaking anything. Respond within the business day.

---

### P3_DiskSpaceLow

**What it means:** Root filesystem is below 20% free. This affects both the monitoring laptop (Docker data) and the remote server (log files, web content).

```bash
# Check which machine triggered (see alert labels — job: node-exporter-local vs node-exporter-remote)

# For monitoring laptop disk:
df -h /
docker system df
# Clean Docker:
docker image prune -f
docker volume prune -f  # CAUTION: only prune volumes not in use

# For remote server disk:
ssh user@<remote_ip>
df -h /
sudo du -sh /var/log/* | sort -rh | head -20
sudo journalctl --disk-usage
# Rotate/clear old logs:
sudo journalctl --vacuum-time=7d
sudo find /var/log -name "*.gz" -mtime +30 -delete
```

---

### P3_HighMemoryUsage

**What it means:** System memory above 85% for 30 minutes. Not immediately dangerous but indicates the server is running close to its limit.

```bash
# Check memory usage breakdown
ssh user@<remote_ip>
free -h
ps aux --sort=-%mem | head -15
sudo cat /proc/meminfo | grep -E "MemTotal|MemFree|MemAvailable|Cached|SwapUsed"

# Check for memory leaks in PHP workers
sudo ps aux | grep php-fpm | awk '{sum+=$6} END {print "Total PHP-FPM RSS: " sum/1024 " MB"}'

# Check if any OOM kills happened recently
sudo dmesg | grep -i "oom\|out of memory" | tail -20
```

---

### P3_HighCPUUsage

**What it means:** Average CPU above 85% for 30 minutes. Investigate for runaway processes or under-provisioning.

```bash
ssh user@<remote_ip>
# Top CPU consumers
top -bn1 | head -20
ps aux --sort=-%cpu | head -15

# Check for PHP cron or batch jobs
sudo crontab -l -u www-data 2>/dev/null
sudo ps aux | grep -E "artisan|cron|wp-cron" | grep -v grep
```

---

### P3_PlatformWarningRateElevated

**What it means:** Warning, notice, or deprecated messages are elevated in application logs. No immediate breakage, but these often precede errors.

```bash
# Check log stream in Grafana
# http://10.10.2.77:3000/d/error-groups-v2
# Filter severity: warning/notice

# Check in Loki (LogQL)
# {platform=~"php-fpm|laravel|nginx"} |~ "(?i)(warning|deprecated|notice)"

# On remote server, review recent PHP warnings
ssh user@<remote_ip>
sudo tail -500 /var/log/php8.4-fpm.log | grep -i "warning\|deprecated" | sort | uniq -c | sort -rn | head -20
```

---

## Auto-Remediation Reference

### Script Inventory

| Script | Location | Trigger | Action | Verification Command |
|---|---|---|---|---|
| `fpm-reload.sh` | `remediate/fpm-reload.sh` | `remediation: fpm-reload` | SSH → `systemctl reload php8.4-fpm` | `systemctl status php8.4-fpm --no-pager` |
| `nginx-file-limit.sh` | `remediate/nginx-file-limit.sh` | `remediation: nginx-file-limit` | SSH → `sysctl -w fs.file-max=100000 && systemctl reload nginx` | `sysctl fs.file-max && systemctl status nginx --no-pager` |
| `redis-flush-cache.sh` | `remediate/redis-flush-cache.sh` | `remediation: redis-flush-cache` | Flush Redis cache DB (DB0) only | `redis-cli info memory \| grep used_memory_human` |
| `generic_triage.sh` | `remediate/generic_triage.sh` | `remediation: generic_triage` | Query Prometheus + Loki → dispatch to specific script | Depends on dispatched script |

### How to Check if Remediation Ran

```bash
# Full remediation log
tail -100 /path/to/Automation-DevOps/auto-remediation.log

# Filter by specific script
grep "fpm-reload" /path/to/Automation-DevOps/auto-remediation.log | tail -20

# Check webhook service status (shows last 20 log lines in browser)
curl http://10.10.2.77:5051/
```

### How to Manually Trigger a Remediation Script

```bash
cd /path/to/Automation-DevOps

# Run fpm-reload manually
bash remediate/fpm-reload.sh

# Run nginx-file-limit manually
bash remediate/nginx-file-limit.sh

# Run redis-flush-cache manually
bash remediate/redis-flush-cache.sh

# Run generic triage manually
bash remediate/generic_triage.sh
```

### How to Disable Auto-Remediation

```bash
# 1. Edit config.json
nano /path/to/Automation-DevOps/config.json
# Change: "auto_remediate": true  →  "auto_remediate": false

# 2. Reload webhook service (it re-reads config on every request, so this is instant)
# No restart needed — the webhook reads config.json dynamically.

# 3. Verify
curl http://localhost:5051/health
# Expected: {"status": "ok", "auto_remediate": false}

# 4. Re-enable when maintenance is complete
# Change auto_remediate back to true in config.json
```

---

## Manual Recovery Procedures

### Restart PHP-FPM

```bash
ssh user@<remote_ip>

# Graceful reload (preferred — drains in-flight requests)
sudo systemctl reload php8.4-fpm

# Hard restart (use if reload fails)
sudo systemctl restart php8.4-fpm

# Verify
sudo systemctl status php8.4-fpm
# Check workers recovered:
curl http://localhost/fpm-status 2>/dev/null || echo "Status page not configured"
```

### Reload Nginx

```bash
ssh user@<remote_ip>

# Test config first
sudo nginx -t

# Graceful reload (zero downtime)
sudo systemctl reload nginx

# Hard restart (only if reload fails)
sudo systemctl restart nginx

# Verify
sudo systemctl status nginx
curl -I http://localhost 2>/dev/null | head -5
```

### Flush Redis Cache Safely

```bash
# This must be done carefully — only flush the cache DB, never session DB
# Verify which DB is cache and which is session:
python3 -c "import json; c=json.load(open('/path/to/Automation-DevOps/config.json')); print(f'Cache DB: {c[\"redis\"][\"cache_db\"]}, Session DB: {c[\"redis\"][\"session_db\"]}')"

# Connect to Redis on remote server
redis-cli -h <remote_ip> -p 6379

# Check DB sizes first
SELECT 0
DBSIZE          # cache DB key count
SELECT 1
DBSIZE          # session DB key count
# ONLY flush the cache DB:
SELECT 0
FLUSHDB

# Verify sessions untouched:
SELECT 1
DBSIZE          # should be same as before
EXIT
```

### Clear Disk Space

```bash
# On monitoring laptop (free Docker disk):
docker image prune -f          # remove dangling images
docker builder prune -f        # clear build cache
# Remove old Prometheus data (reduces TSDB size):
# Edit docker-compose.yml: change --storage.tsdb.retention.time=15d to 7d
# Then: docker compose restart prometheus

# On remote server (free application disk):
ssh user@<remote_ip>
sudo journalctl --vacuum-time=7d          # clear systemd journal > 7 days
sudo find /var/log -name "*.gz" -mtime +14 -delete  # delete old compressed logs
sudo find /tmp -mtime +1 -delete          # clear old temp files
sudo apt-get autoremove -y 2>/dev/null    # remove unused packages
df -h /                                   # verify improvement
```

### Restart the Docker Monitoring Stack

```bash
cd /path/to/Automation-DevOps

# Restart all containers
docker compose restart

# Or restart individual services (see Docker Stack Management below)

# Full stop and start (use only if restart fails)
docker compose down
docker compose up -d

# Wait for health checks to pass
docker compose ps
# All containers should show "healthy" or "running"
```

### Restart the Webhook Service

```bash
# Via systemd (preferred)
sudo systemctl restart auto-remediation-webhook
sudo systemctl status auto-remediation-webhook

# Verify it's responding
curl http://localhost:5051/health

# View recent logs
sudo journalctl -u auto-remediation-webhook -n 50

# If systemd not available, run manually with gunicorn:
cd /path/to/Automation-DevOps
gunicorn --workers 2 --threads 4 --bind 0.0.0.0:5051 webhook:app &
```

---

## Docker Stack Management

### Start / Stop / Restart Individual Services

```bash
cd /path/to/Automation-DevOps

# Start all
docker compose up -d

# Stop all (preserves data volumes)
docker compose stop

# Restart a single service
docker compose restart prometheus
docker compose restart loki
docker compose restart alertmanager
docker compose restart grafana
docker compose restart pushgateway

# Stop and remove a single container (data volume preserved)
docker compose down prometheus  # then: docker compose up -d prometheus
```

### View Logs

```bash
cd /path/to/Automation-DevOps

# Follow all service logs
docker compose logs -f

# Follow a specific service
docker compose logs -f prometheus
docker compose logs -f loki
docker compose logs -f alertmanager
docker compose logs -f grafana

# Last 100 lines, no follow
docker compose logs --tail 100 prometheus
```

### Check Container Health

```bash
cd /path/to/Automation-DevOps
docker compose ps

# Individual health endpoints
curl -s http://localhost:9090/-/healthy   # Prometheus
curl -s http://localhost:9093/-/healthy   # Alertmanager
curl -s http://localhost:3100/ready       # Loki
curl -s http://localhost:3000/api/health  # Grafana
curl -s http://localhost:9091/-/healthy   # Pushgateway
curl -s http://localhost:5051/health      # Webhook
```

### Reload Configurations Without Restart

```bash
# Reload Prometheus config and rules (no restart needed)
curl -X POST http://localhost:9090/-/reload

# Reload Alertmanager routing config
curl -X POST http://localhost:9093/-/reload

# Reload Loki rules (if supported by version)
curl -X POST http://localhost:3100/loki/api/v1/rules/reload 2>/dev/null || \
  docker compose restart loki
```

---

## How to Disable Auto-Remediation

Use this during planned maintenance to prevent scripts from running while you are making changes on the remote server.

```bash
# Step 1: Edit config.json
nano /path/to/Automation-DevOps/config.json
# Set: "auto_remediate": false

# Step 2: No regeneration needed — webhook.py reads config.json dynamically on every request
# However, if you also need to regenerate email templates or other configs:
python3 generate_configs.py

# Step 3: Verify auto-remediation is disabled
curl http://localhost:5051/health
# Expected output: {"status": "ok", "auto_remediate": false}

# Step 4: Perform your maintenance

# Step 5: Re-enable
nano /path/to/Automation-DevOps/config.json
# Set: "auto_remediate": true

# Step 6: Verify re-enabled
curl http://localhost:5051/health
# Expected output: {"status": "ok", "auto_remediate": true}
```

---

## Config Change Procedure

Safe way to change any configuration value and reload without downtime.

```bash
# Step 1: Back up current config
cp /path/to/Automation-DevOps/config.json /path/to/Automation-DevOps/config.json.bak.$(date +%Y%m%d%H%M%S)

# Step 2: Edit config.json
nano /path/to/Automation-DevOps/config.json

# Step 3: Validate the new config (check-only mode)
cd /path/to/Automation-DevOps
python3 generate_configs.py --check
# Must print "OK" before proceeding

# Step 4: Generate new configs
python3 generate_configs.py

# Step 5: Reload affected services (no restart needed for config-only changes)
curl -X POST http://localhost:9090/-/reload   # Prometheus
curl -X POST http://localhost:9093/-/reload   # Alertmanager

# Step 6: For Loki config changes, a restart is required
docker compose restart loki
# Wait for healthy:
docker compose ps loki

# Step 7: For Grafana env var changes (admin password), restart is required
docker compose restart grafana

# Step 8: Webhook reads config.json dynamically — no action needed
curl http://localhost:5051/health
```

---

## Adding a New Site (Operational Summary)

For full details see README.md. Condensed steps for operators:

```bash
cd /path/to/Automation-DevOps

# 1. Edit prometheus/prometheus.yml.template
#    Add two scrape_configs entries for node-exporter and nginx-exporter
#    pointing to the new server IP, with correct platform and instance_name labels

# 2. Edit promtail/promtail-config.yml.template
#    Add a new scrape_config section for the new server's log files

# 3. Edit prometheus/rules/app-alerts.yaml
#    Add platform-specific alert rules if needed, or extend existing rules
#    to include the new platform in match expressions

# 4. Edit loki/rules/fake/loki-alerts.yaml
#    Extend platform match regex: platform=~"nginx|php-fpm|mysql|new-platform"

# 5. Regenerate and reload
python3 generate_configs.py
curl -X POST http://localhost:9090/-/reload
curl -X POST http://localhost:9093/-/reload
docker compose restart loki

# 6. Deploy exporters on the new server (see README.md Deployment Guide)

# 7. Verify new targets in Prometheus
# http://10.10.2.77:9090/targets  — new jobs should appear green
```

---

## Log Locations

| Log File | Service | Location | Rotation |
|---|---|---|---|
| Auto-remediation log | Webhook service | `Automation-DevOps/auto-remediation.log` | Manual — `truncate -s 0` after review |
| Webhook service log | Gunicorn / systemd | `Automation-DevOps/remediation-webhook.log` or `journalctl -u auto-remediation-webhook` | systemd journal rotation |
| Prometheus data | Prometheus container | Docker volume `prometheus-data` — 15-day retention | Automatic (TSDB retention) |
| Loki data | Loki container | Docker volume `loki-data` | Configured in `loki/loki-config.yml` |
| Nginx access log | Nginx on remote server | `/var/log/nginx/access.log` | logrotate daily |
| Nginx error log | Nginx on remote server | `/var/log/nginx/error.log` | logrotate daily |
| PHP-FPM log | PHP-FPM on remote server | `/var/log/php8.4-fpm.log` | logrotate weekly |
| MySQL slow query log | MySQL on remote server | `/var/log/mysql/mysql-slow.log` | logrotate weekly |
| MySQL error log | MySQL on remote server | `/var/log/mysql/error.log` | logrotate weekly |
| Redis log | Redis on remote server | `/var/log/redis/redis-server.log` | logrotate daily |
| Promtail log | Promtail on remote server | `journalctl -u promtail` | systemd journal rotation |
| Laravel app log | Laravel on remote server | `/var/www/*/storage/logs/laravel.log` | Manual or application-configured |
| Docker container logs | All Docker services | `docker compose logs <service>` | Docker json-file driver (default 100MB) |

---

## Common Issues and Fixes

**1. Alertmanager sends email but webhook is never called**

```bash
# Verify the remediation label is present on the alert
curl -s http://localhost:9093/api/v2/alerts | python3 -m json.tool | grep remediation

# Check Alertmanager routing config
curl -s http://localhost:9093/api/v2/status | python3 -m json.tool | grep -A5 "route"

# Verify webhook URL in alertmanager.yml matches actual webhook service
grep "webhook" /path/to/Automation-DevOps/alertmanager/alertmanager.yml
# Should show: http://10.10.2.77:5051/webhook
```

**2. All containers start but Prometheus shows no data from remote server**

```bash
# Check network connectivity from monitoring laptop to remote server
ping <remote_ip>
nc -zv <remote_ip> 9100  # Node Exporter
nc -zv <remote_ip> 9113  # Nginx Exporter
nc -zv <remote_ip> 9080  # Promtail

# Check firewall on remote server
ssh user@<remote_ip>
sudo ufw status | grep -E "9100|9113|9080"
# If blocked: sudo ufw allow from 10.10.2.77 to any port 9100
```

**3. Loki alert rules not appearing in Alertmanager**

```bash
# Check Loki ruler is active
curl -s http://localhost:3100/ruler/ring
curl -s http://localhost:3100/loki/api/v1/rules

# Check for YAML syntax errors in rule files
docker logs loki | grep -i "error\|warn" | tail -30

# Verify rules directory is mounted
docker compose exec loki ls /etc/loki/rules/fake/
```

**4. Webhook service crashes on startup**

```bash
sudo journalctl -u auto-remediation-webhook -n 50

# Most common cause: missing config.json or wrong path
ls -la /path/to/Automation-DevOps/config.json

# Missing Python dependencies
cd /path/to/Automation-DevOps && pip3 install -r requirements.txt

# Port already in use
sudo lsof -i :5051
```

**5. Redis flush script aborts with "redis-cli unreachable"**

```bash
# Test Redis connectivity
redis-cli -h <remote_ip> -p 6379 ping

# If Redis requires a password, verify in config.json
python3 -c "import json; print(json.load(open('config.json'))['redis']['password'])"

# Check Redis is running on remote server
ssh user@<remote_ip>
sudo systemctl status redis-server
```

**6. generate_configs.py regenerates files but Prometheus/Alertmanager still use old config**

```bash
# Config files are read at reload/start — always call the reload API
curl -X POST http://localhost:9090/-/reload
curl -X POST http://localhost:9093/-/reload

# If reload fails, check config syntax first
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
```

**7. Playwright CWV metrics are stale (last updated hours ago)**

```bash
# Check crontab on remote server
ssh user@<remote_ip>
crontab -l  # look for */3 * * * * playwright-probe entry

# Check Pushgateway for last push time
curl -s http://10.10.2.77:9091/metrics | grep push_time_seconds

# Run probe manually to test
# (run the Playwright probe script directly on remote server)
bash /path/to/playwright-probe.sh

# Check Pushgateway is reachable from remote server
curl http://10.10.2.77:9091/metrics | head -5
```

---

## Post-Incident Checklist

Complete this after every P1 incident, before closing.

```bash
# 1. Confirm the alert has resolved in Alertmanager
# http://10.10.2.77:9093 — no active P1 alerts

# 2. Confirm service is healthy on remote server
ssh user@<remote_ip>
sudo systemctl status nginx php8.4-fpm mysql redis-server

# 3. Confirm 5xx error rate is back to baseline
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=sum(rate(nginx_http_requests_total{status=~"5.."}[5m])) / sum(rate(nginx_http_requests_total[5m])) * 100' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else '0')"
# Target: below 0.1%

# 4. Confirm LCP is within acceptable range
curl -sG 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=max(probe_lcp_ms)' \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 'no data')"
# Target: below 2500ms (good), below 4000ms (acceptable)

# 5. Review full remediation log for the incident
grep -A5 "RCA INVESTIGATION STARTED" /path/to/Automation-DevOps/auto-remediation.log | tail -50

# 6. Check the resolved email was sent
# The webhook sends a resolved email with RCA summary when Alertmanager fires the resolved event.
# If no email received, check: docker logs alertmanager | grep resolved

# 7. Document in incident log (manual step)
# Record: time of alert, root cause identified, action taken, time to resolve, any config changes needed.

# 8. If the incident revealed a gap in alerting or remediation:
# - Add or tune an alert rule in prometheus/rules/ or loki/rules/fake/
# - Add a new remediation script to remediate/ if needed
# - Run: python3 generate_configs.py && curl -X POST http://localhost:9090/-/reload

# 9. Re-enable auto-remediation if it was disabled during maintenance
curl http://localhost:5051/health
# If auto_remediate is false: edit config.json → set true → verify health endpoint
```
