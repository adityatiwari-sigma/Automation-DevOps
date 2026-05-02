# Alerts Catalog — AIOps Platform

This document is the authoritative reference for every alert defined in the AIOps monitoring stack. It covers all 25 active alert rules across five rule files: `prometheus/rules/app-alerts.yaml`, `prometheus/rules/correlation-alerts.yaml`, `prometheus/rules/self-monitoring.yaml`, `loki/rules/fake/loki-alerts.yaml`, and `loki/rules/fake/correlation-rules.yaml`. For each alert, this catalog describes the triggering condition in plain language, the business impact on end users, the most common root causes, the exact alert expression, step-by-step remediation guidance, and the conditions that require human escalation beyond auto-remediation. Engineers responding to a firing alert should use this document as their first reference.

---

## P1 Critical Alerts

P1 alerts represent immediate production incidents. They are routed to the `oncall` recipient with a repeat interval of 1 hour. Auto-remediated P1 alerts also fire to the remediation webhook concurrently with the email notification.

---

### P1_5xxErrorRateCritical
**Severity:** P1
**Source:** Prometheus metrics
**Stabilization Window:** for: 2m
**Auto-Remediated:** No (routes to generic_triage via remediation label)
**Triggers When:** The ratio of HTTP 5xx responses to all HTTP responses, computed as a 2-minute rate, exceeds 5% and remains above that threshold for 2 continuous minutes. Uses the `promtail_custom_nginx_request_duration_ms_count` Histogram metric produced by Promtail's nginx-access pipeline.
**Business Impact:** More than 1 in 20 user requests are failing with server-side errors. Users attempting to load pages, submit forms, or complete purchases will see error pages or blank responses. At 5% error rate, checkout conversion drops measurably and user trust is impacted.
**Root Cause:** PHP-FPM worker pool exhaustion (upstream 502), unhandled PHP exceptions causing 500 responses, database connection failures, recent bad deployment, memory limit exhaustion (`allowed memory size exhausted` fatal), or nginx upstream timeout.
**Alert Expression:**
```promql
(
  sum(rate(promtail_custom_nginx_request_duration_ms_count{status=~"5.."}[2m]))
  /
  sum(rate(promtail_custom_nginx_request_duration_ms_count[2m]))
) > 0.05
```
**Remediation Steps:**
1. Check the Grafana error-groups dashboard at `http://10.10.2.77:3000/d/error-groups-v2`.
2. Query Loki for recent PHP-FPM errors: `{platform="php-fpm"} |~ "error|fatal"` over the last 10 minutes.
3. Check PHP-FPM worker pool: `{platform="php-fpm"} |~ "max_children"`.
4. If workers are exhausted: the `FPM_WorkerPoolExhaustion` Loki alert should have already triggered auto-remediation (fpm-reload). Verify the reload ran by checking `auto-remediation.log`.
5. If PHP-FPM was reloaded but errors persist, check for a bad deployment: `git log --oneline -10` on the remote server.
6. If errors are database-related (MySQL connection errors in PHP logs): check `{platform="mysql"}` logs in Loki.
**Escalate If:** Auto-remediation does not reduce the error rate within 5 minutes; error rate exceeds 25%; PHP-FPM reload fails via SSH; a bad deployment is identified (requires rollback).

---

### P1_5xxCountSpike
**Severity:** P1
**Source:** Prometheus metrics
**Stabilization Window:** for: 1m
**Auto-Remediated:** No (routes to generic_triage)
**Triggers When:** The total count of HTTP 5xx responses in the last 2 minutes exceeds 50, computed using `nginx_http_requests_total` from the Nginx Exporter. The 1-minute `for` window makes this alert fast-firing compared to the rate-based `P1_5xxErrorRateCritical`.
**Business Impact:** A sudden burst of 50+ errors in 2 minutes indicates a service disruption event rather than a gradual degradation. Users across all concurrent sessions are impacted simultaneously — this pattern is consistent with a process crash, a deployment gone wrong, or a database going offline.
**Root Cause:** Service restart mid-traffic (nginx or PHP-FPM restarted), deployment error that crashed the PHP-FPM master process, database connection pool exhausted, out-of-memory kill of a critical process, or a sudden traffic spike hitting a resource limit.
**Alert Expression:**
```promql
sum(increase(nginx_http_requests_total{status=~"5.."}[2m])) > 50
```
**Remediation Steps:**
1. Check the time of the spike against recent deployment or cron activity on the remote server.
2. Verify nginx is running: check `{platform="nginx"} |~ "error"` in Loki for the spike window.
3. Check PHP-FPM status via SSH: `systemctl status php8.4-fpm --no-pager`.
4. Review recent `auto-remediation.log` entries to see if any action was already taken.
5. If the spike is a single burst and error rate has returned to normal, monitor for recurrence.
6. If the spike is ongoing, follow the `P1_5xxErrorRateCritical` remediation steps.
**Escalate If:** Count continues rising (not a one-time burst); service processes are found not running; a deployment rollback is needed.

---

### PHPFPMWorkerPoolExhaustion
**Severity:** P1
**Source:** Prometheus metrics
**Stabilization Window:** for: 1m
**Auto-Remediated:** Yes (generic_triage.sh)
**Triggers When:** The ratio of active PHP-FPM worker processes to the configured `max_children` limit in the `www` pool exceeds 90%, sustained for 1 minute. Uses `phpfpm_active_processes` and `phpfpm_max_active_processes` metrics from the PHP-FPM status page exporter.
**Business Impact:** With 90%+ workers occupied, any new incoming request must wait for a worker to free up. Response times spike immediately (new requests queue), and once the pool is 100% full, requests are rejected outright (502 Bad Gateway from nginx). Checkout, login, and all dynamic page loads are affected.
**Root Cause:** Slow database queries holding PHP workers blocked on DB I/O, a traffic surge beyond the configured pool size, a slow external API call (payment gateway, shipping API) blocking workers, memory pressure causing slow garbage collection, or a runaway PHP script consuming a worker indefinitely.
**Alert Expression:**
```promql
phpfpm_active_processes{pool="www"} / phpfpm_max_active_processes{pool="www"} > 0.90
```
**Remediation Steps:**
1. `generic_triage.sh` fires automatically — check `auto-remediation.log` for the outcome.
2. Verify the reload had effect: SSH to 10.10.2.21 and run `php-fpm8.4 -t` (test config) then `systemctl status php8.4-fpm`.
3. Identify which scripts are slow: query Loki for `{platform="php-fpm"} |~ "slow"` or check PHP slow-log if enabled.
4. Check database query times: `{platform="mysql"} |~ "Query_time"` in Loki.
5. Consider temporarily increasing `pm.max_children` on the remote server if traffic is legitimately elevated.
**Escalate If:** `fpm-reload` (triggered via generic_triage) fails via SSH; worker pool stays above 90% after reload; a slow external dependency is identified that cannot be resolved by reloading PHP-FPM.

---

### HighHTTP5xxRate
**Severity:** P1
**Source:** Prometheus metrics
**Stabilization Window:** for: 2m
**Auto-Remediated:** Yes (generic_triage.sh)
**Triggers When:** The rate of HTTP 5xx responses from the Nginx Exporter's `nginx_http_requests_total` metric exceeds 5% of total requests for 2 minutes. This is the auto-remediation counterpart to `P1_5xxErrorRateCritical` — both measure the same business condition but from different metric sources (Nginx Exporter counts vs. Promtail Histogram counts) and the remediation label on this alert triggers the automated response.
**Business Impact:** Same as `P1_5xxErrorRateCritical` — more than 1 in 20 requests are failing. The duplicate coverage ensures that even if the Promtail pipeline stops producing `promtail_custom_nginx_*` metrics, the Nginx Exporter-sourced alert still fires and triggers remediation.
**Root Cause:** See `P1_5xxErrorRateCritical`. The most common trigger for the auto-remediation path is PHP-FPM worker pool exhaustion producing 502 errors.
**Alert Expression:**
```promql
sum(rate(nginx_http_requests_total{status=~"5.."}[2m]))
/
sum(rate(nginx_http_requests_total[2m])) > 0.05
```
**Remediation Steps:**
1. `generic_triage.sh` fires automatically — it queries both Loki and Prometheus to determine root cause and routes to `fpm-reload.sh`, `nginx-file-limit.sh`, or `redis-flush-cache.sh` as appropriate.
2. Check `auto-remediation.log` for the decision and outcome.
3. If auto-remediation reports SUCCESS but the error rate remains elevated, escalate.
**Escalate If:** Error rate does not drop below 5% within 10 minutes of auto-remediation; `generic_triage.sh` times out (90-second limit); SSH connection to 10.10.2.21 fails.

---

### FPM_WorkerPoolExhaustion
**Severity:** P1
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** Yes (fpm-reload.sh)
**Triggers When:** At least one log line matching the pattern `server reached pm.max_children` or `max_children` (case-insensitive) appears in the `php-fpm` platform stream within the last 1 minute. The `for: 0m` setting means this fires the moment the condition is true — no stabilization delay.
**Business Impact:** PHP-FPM has hit its hard worker limit. New incoming PHP requests are rejected immediately. Users see 502 Bad Gateway responses from nginx. The impact is instantaneous and complete — there is no degraded state, only total failure for new requests until a worker frees up.
**Root Cause:** PHP-FPM logged the exact message "server reached pm.max_children setting" — this is the authoritative log-level signal that the pool is exhausted. The Prometheus-based `PHPFPMWorkerPoolExhaustion` may not have fired yet because the metric-based check requires a 1-minute sustained condition; this Loki rule fires on the first occurrence.
**Alert Expression:**
```logql
sum(count_over_time({platform="php-fpm"} |~ "(?i)(server reached pm.max_children|max_children)" [1m])) > 0
```
**Remediation Steps:**
1. `fpm-reload.sh` triggers automatically via SSH: `systemctl reload php8.4-fpm`.
2. A reload sends `SIGUSR2` to PHP-FPM, causing it to gracefully recycle idle workers without dropping in-flight requests.
3. Verify in Loki that the `max_children` log lines stop appearing after the reload.
4. Long-term: review `pm.max_children`, `pm.start_servers`, `pm.max_spare_servers` in `/etc/php/8.4/fpm/pool.d/www.conf`.
**Escalate If:** `fpm-reload.sh` fails; log lines continue appearing after reload; PHP-FPM process count remains at maximum; a slow-query or external API dependency is identified as the cause of workers blocking.

---

### Nginx_TooManyOpenFiles
**Severity:** P1
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** Yes (nginx-file-limit.sh)
**Triggers When:** At least one log line matching `too many open files` (case-insensitive) appears in the `nginx` platform stream within the last 1 minute. This error appears in nginx's error log when the process hits the OS file descriptor limit (`fs.file-max` or the per-process `nofile` ulimit).
**Business Impact:** Nginx cannot open new connections, log files, or upstream connections once the file descriptor limit is exhausted. New user connections are refused with connection errors (not even an HTTP response). This is a hard service outage for all new connections.
**Root Cause:** The Linux kernel's `fs.file-max` sysctl is set too low for the server's current load, or the systemd `LimitNOFILE` for the nginx service unit is not configured. Traffic spikes, a connection leak in an upstream application, or a large number of kept-alive connections from a CDN can trigger this.
**Alert Expression:**
```logql
sum(count_over_time({platform="nginx"} |~ "(?i)too many open files" [1m])) > 0
```
**Remediation Steps:**
1. `nginx-file-limit.sh` triggers automatically via SSH:
   - Runs `sysctl -w fs.file-max=100000` to increase the kernel limit.
   - Runs `systemctl reload nginx` to apply without dropping connections.
2. Verify with Loki that `too many open files` errors stop appearing.
3. For a permanent fix, add `fs.file-max = 100000` to `/etc/sysctl.d/99-aiops.conf` on the remote server and add `LimitNOFILE=65536` to the nginx systemd unit override.
**Escalate If:** `sysctl` command fails via SSH (requires sudo); nginx reload fails; error continues after the limit increase (indicates the new limit was also insufficient); a connection leak is suspected.

---

### Redis_Memory_High
**Severity:** P1
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** Yes (redis-flush-cache.sh)
**Triggers When:** At least one log line matching `OOM command not allowed` or `OOM killer` (case-insensitive) appears in the `redis` platform stream within the last 1 minute. These messages appear in Redis's own log when it has reached `maxmemory` and the configured eviction policy cannot free enough space.
**Business Impact:** Redis is refusing write commands because it has hit its memory ceiling. Session writes, cache warming, shopping cart persistence, and any Laravel/WordPress cache operations that attempt a write will fail. Read operations may still succeed depending on the eviction policy, but the application will likely throw exceptions on cache writes, producing 500 errors visible to users.
**Root Cause:** The application cache has grown to fill Redis's configured `maxmemory`. Common causes: a cache invalidation bug that prevented old keys from expiring, abnormally large session data, a traffic spike that cached many more pages than usual, or `maxmemory` set too conservatively for the current dataset size.
**Alert Expression:**
```logql
sum(count_over_time({platform="redis"} |~ "(?i)OOM command not allowed|OOM killer" [1m])) > 0
```
**Remediation Steps:**
1. `redis-flush-cache.sh` triggers automatically with three safety guards:
   - Guard 1: Aborts if `auto_remediate=false` in config.json.
   - Guard 2: Aborts if `cache_db == session_db` (prevents accidental session destruction).
   - Guard 3: Only runs `FLUSHDB` if memory ratio is actually above 90%.
2. The script runs `redis-cli -n <cache_db> FLUSHDB` — flushes only the cache database (default DB 0), leaving the session database (DB 1) untouched.
3. Verify recovery: `redis-cli info memory | grep used_memory_human` should show a sharp drop.
4. Long-term: review `maxmemory` setting in `/etc/redis/redis.conf` and consider implementing proper cache TTLs at the application level.
**Escalate If:** `redis-flush-cache.sh` aborts due to `cache_db == session_db` (configuration error in config.json); FLUSHDB fails; OOM errors continue after flush (indicates session DB or persistent data filling memory); Redis process is killed by the Linux OOM killer.

---

### P1_CheckoutPaymentFatal
**Severity:** P1
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** Yes (generic_triage.sh)
**Triggers When:** At least one log line matching both a fatal severity keyword (`fatal`, `emergency`, `critical`) AND a checkout/payment URI pattern (`/checkout`, `/payment/`, `/cart`, `/order`, `/billing`, `/invoice`) appears across any of the platform streams (`nginx`, `php-fpm`, `mysql`, `wordpress`, `laravel`) within the last 1 minute.
**Business Impact:** A fatal error occurred in the payment or checkout flow. Users attempting to complete purchases are experiencing hard failures. Revenue impact is direct and immediate — every occurrence of this alert during business hours represents a potentially lost transaction.
**Root Cause:** Payment gateway API returning an unexpected response that the application fails to handle, a database error during order creation, a PHP fatal error in checkout controller code (undefined method, out-of-memory), a Stripe/PayPal webhook handler crash, or a session expiration causing a null reference during checkout.
**Alert Expression:**
```logql
sum(count_over_time(
  {platform=~"nginx|php-fpm|mysql|wordpress|laravel"}
  |~ `(?i)(fatal|emergency|critical)`
  |~ `(?i)(/checkout|/payment/|/cart|/order|/billing|/invoice)`
  [1m])) > 0
```
**Remediation Steps:**
1. `generic_triage.sh` fires automatically to collect triage data.
2. Query Loki immediately: `{platform=~"php-fpm|laravel|wordpress"} |~ "fatal|critical" |~ "checkout|payment"` for the last 15 minutes.
3. Identify the exact file, line number, and error message from the log.
4. Check if the error is isolated to one SKU, payment method, or user account.
5. Review payment gateway status pages (Stripe, PayPal) for external outages.
6. If a code error: roll back the last deployment if the error correlates with a recent deploy.
**Escalate If:** More than 5 occurrences in 10 minutes; a payment gateway API key is invalid or expired; database integrity issues are found; the error cannot be reproduced or identified — escalate to senior developer immediately.

---

### LcpDegraded_CpuBottleneck
**Severity:** P1
**Source:** Prometheus metrics
**Stabilization Window:** for: 2m
**Auto-Remediated:** No
**Triggers When:** The maximum LCP value from Pushgateway (`probe_lcp_ms`) exceeds 4000ms AND the CPU utilization on the remote server (computed from `node_cpu_seconds_total` with `instance_name="dev-regenics"`) exceeds 85%, both conditions sustained for 2 minutes. The `and on()` operator joins these two unrelated metric series by scalar comparison.
**Business Impact:** Users are experiencing sluggish page loads with LCP above 4 seconds — Google's "Poor" threshold. The root cause is identified as CPU saturation, meaning the server cannot process PHP requests fast enough. This directly impacts Core Web Vitals scores, SEO rankings (Google uses CWV for ranking signals), and user bounce rates.
**Root Cause:** A runaway PHP process consuming excessive CPU, a heavy background job (WordPress cron, Magento reindex, report generation) running during peak traffic, a slow database query causing PHP workers to spin-wait, a DDoS or bot traffic surge, or insufficient server resources for the current traffic load.
**Alert Expression:**
```promql
(max(probe_lcp_ms) > 4000)
and on()
((100 - (avg(rate(node_cpu_seconds_total{mode="idle",instance_name="dev-regenics"}[5m])) * 100)) > 85)
```
**Remediation Steps:**
1. SSH to 10.10.2.21 and run `top -b -n1 | head -20` to identify the CPU-consuming process.
2. Check for runaway PHP workers: `ps aux | grep php-fpm | sort -k3 -n`.
3. Check for WordPress or Magento background jobs consuming CPU: look for `cron.php` or `bin/magento` in process list.
4. Check the LCP correlation dashboard: `http://10.10.2.77:3000/d/lcp-correlation`.
5. If CPU is consumed by legitimate traffic: consider enabling nginx rate limiting or temporarily scaling the PHP-FPM pool.
6. If a single process is runaway: `kill -9 <pid>` the offending process after capturing a stack trace.
**Escalate If:** CPU does not drop after killing runaway processes; the traffic spike is a DDoS requiring upstream mitigation (contact hosting/CDN provider); server resources are genuinely insufficient and a scale-up is needed.

---

## P2 High Alerts

P2 alerts indicate degraded performance that impacts user experience but does not constitute a complete service outage. They are routed to the team recipient with a repeat interval of 4 hours and require investigation within 1 hour.

---

### P2_HighRequestLatency
**Severity:** P2
**Source:** Prometheus metrics
**Stabilization Window:** for: 5m
**Auto-Remediated:** No
**Triggers When:** The 95th percentile of HTTP request duration (computed from the `promtail_custom_nginx_request_duration_ms_bucket` Histogram) exceeds 4500ms, sustained for 5 minutes. This is an aggregate across all URIs and status codes.
**Business Impact:** 5% of users are waiting more than 4.5 seconds for page responses. At P95 = 4500ms, the median (P50) user is likely experiencing 1-2 second latencies, which is above Google's recommended thresholds and significantly impacts perceived performance and conversion rates.
**Root Cause:** Slow database queries, PHP-FPM workers blocked on external API calls (payment gateways, shipping providers), Redis cache misses causing cache-bypass database reads, large page sizes (images, uncached full-page renders), or server resource contention.
**Alert Expression:**
```promql
histogram_quantile(0.95, sum(rate(promtail_custom_nginx_request_duration_ms_bucket[5m])) by (le)) > 4500
```
**Remediation Steps:**
1. Check if `P2_SlowURLLatency` is also firing to identify specific slow endpoints.
2. Query Loki for slow MySQL queries: `{platform="mysql"} |~ "Query_time"` over the last 15 minutes.
3. Check PHP-FPM pool utilization in Grafana — is the pool near exhaustion?
4. Review nginx access logs for the slowest requests: `{platform="nginx"} | json | response_time_ms > 4000`.
5. Check if a recent deployment correlates with the latency increase.
6. Inspect Redis cache hit rate — a sudden drop indicates a cache stampede or invalidation event.
**Escalate If:** P95 latency exceeds 10 seconds; the issue persists beyond 30 minutes without improvement; a database performance issue is identified that requires a DBA to analyze query plans.

---

### P2_SlowURLLatency
**Severity:** P2
**Source:** Prometheus metrics
**Stabilization Window:** for: 5m
**Auto-Remediated:** No
**Triggers When:** The P95 request duration for any individual URI (after query-string stripping by the Promtail pipeline) exceeds 4500ms, sustained for 5 minutes. This is the per-URI breakdown of `P2_HighRequestLatency` — it fires with a `uri` label identifying the specific slow endpoint.
**Business Impact:** A specific page or API endpoint is performing significantly worse than others. Users who navigate to that URI experience slow page loads. The `uri` label in the alert makes it immediately actionable — the engineer knows exactly which endpoint to investigate.
**Root Cause:** An unoptimized database query specific to that endpoint, a missing cache layer for that URI, an external API call made synchronously on that page, a Magento/WooCommerce product page with many variants causing slow rendering, or a heavy Eloquent relationship not using eager loading.
**Alert Expression:**
```promql
histogram_quantile(0.95, sum by (le, uri) (rate(promtail_custom_nginx_request_duration_ms_bucket[5m]))) > 4500
```
**Remediation Steps:**
1. Note the `uri` label from the alert — this is the specific endpoint.
2. Query Loki for that URI: `{platform="nginx"} | json | uri = "/the/slow/path" | line_format "{{.response_time_ms}}"`.
3. Check if the endpoint hits MySQL: look for slow queries in `{platform="mysql"}` correlated by timestamp.
4. Add Loki query time filter: `{platform="mysql"} |~ "Query_time" | logfmt | Query_time > 2`.
5. Profile the endpoint if possible (Laravel Telescope, Xdebug profiling in staging).
6. Consider adding a Redis cache layer for expensive queries behind this endpoint.
**Escalate If:** URI is a checkout or payment endpoint (escalate severity to P1); the slow query requires a schema change or new index (schedule with DBA); P95 > 15 seconds.

---

### P2_UpstreamConnectionErrors
**Severity:** P2
**Source:** Prometheus metrics
**Stabilization Window:** for: 5m
**Auto-Remediated:** No
**Triggers When:** The rate of HTTP 502 (Bad Gateway), 503 (Service Unavailable), and 504 (Gateway Timeout) responses combined exceeds 0.5 requests per second, sustained for 5 minutes. These status codes specifically indicate nginx failed to connect to or get a response from the PHP-FPM upstream.
**Business Impact:** Users are receiving gateway error pages (502/503) or waiting until nginx times out (504). Unlike 500 errors (which PHP generated), these errors indicate PHP-FPM is not responding at all — a more severe condition affecting all dynamic pages.
**Root Cause:** PHP-FPM process crashed or is restarting, PHP-FPM Unix socket disappeared, misconfigured nginx `fastcgi_pass` directive, PHP-FPM pool overwhelmed and not accepting new connections, a PHP-FPM worker stuck in an infinite loop that consumes all sockets, or a systemd service restart in progress.
**Alert Expression:**
```promql
sum(rate(nginx_http_requests_total{status=~"502|503|504"}[5m])) > 0.5
```
**Remediation Steps:**
1. SSH to 10.10.2.21: `systemctl status php8.4-fpm --no-pager` — is the service running?
2. Check PHP-FPM socket: `ls -la /var/run/php/php8.4-fpm.sock` — does it exist?
3. Review PHP-FPM logs: `{platform="php-fpm"} |~ "error|fatal"` in Loki for the last 5 minutes.
4. If PHP-FPM has crashed: `systemctl restart php8.4-fpm` (restart, not reload).
5. Check nginx error logs: `{platform="nginx"} |~ "upstream"` in Loki.
6. If 504s (timeouts): increase `fastcgi_read_timeout` in nginx config or fix the slow PHP code.
**Escalate If:** PHP-FPM service fails to start after restart attempt; the error is caused by a database being completely unreachable; error rate approaches 100% of requests.

---

### P2_AppFatalRateHigh
**Severity:** P2
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** Yes (generic_triage.sh)
**Triggers When:** At least one log line matching PHP fatal error patterns (`PHP Fatal error`, `PHP Parse error`, `Call to undefined`, `Allowed memory size exhausted`) appears in `php-fpm`, `wordpress`, or `laravel` platform streams in the last 5 minutes. Checkout-related paths are explicitly excluded (those are covered by `P1_CheckoutPaymentFatal`).
**Business Impact:** Non-checkout pages are generating PHP fatal errors. Affected users see white screens or error pages. Since this is P2 (not checkout-affecting), revenue impact is indirect but user experience is degraded on affected pages.
**Root Cause:** A code deployment introduced a PHP parse error, a function call to an undefined method after an incomplete deployment, memory limit exhaustion on a heavy page (import tool, report generation, bulk operation), a missing required PHP extension after a system update, or an incompatible dependency version.
**Alert Expression:**
```logql
sum by (platform) (count_over_time(
  {platform=~"php-fpm|wordpress|laravel"}
  |~ `(?i)(php\s*(fatal|parse)\s*error|fatal\s*error.*php|call\s+to\s+undefined|allowed\s+memory\s+size\s+exhausted)`
  !~ `(?i)(/checkout|/payment/|/cart)`
  [5m])) > 0
```
**Remediation Steps:**
1. Check the `platform` label on the alert to identify which application is affected.
2. Query Loki for the exact error: `{platform="laravel"} |~ "Fatal error|Parse error"` with the full error message.
3. Note the file path and line number from the error message.
4. Check if the error correlates with a recent deployment: `git log --oneline -5` on the remote server.
5. If a deployment is identified: roll back with `git revert` or restore the previous release.
6. If `Allowed memory size exhausted`: find the operation consuming memory and either optimize it or increase `memory_limit` in `php.ini`.
**Escalate If:** Fatal errors spread to checkout pages (escalate to P1); error rate is continuous (not just a single occurrence); a code rollback is needed (requires developer access).

---

### P2_MySQLIssuesHigh
**Severity:** P2
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** Yes (generic_triage.sh)
**Triggers When:** At least one log line matching MySQL error or performance patterns (`error`, `slow query`, `lock time`, `table is full`, `access denied`) appears in the `mysql` platform stream in the last 5 minutes.
**Business Impact:** Database errors or performance issues are impacting the application's data layer. Users may see incomplete page loads, stale data, or errors on any page that requires database access. Slow queries directly cause high PHP-FPM worker wait times, which then triggers the cascade to `PHPFPMWorkerPoolExhaustion`.
**Root Cause:** A missing database index causing a full table scan on a frequently-executed query, MySQL table fragmentation after heavy delete/update activity, InnoDB lock contention from concurrent transactions, `tmp_table_size` / `max_heap_table_size` too small causing on-disk temp tables, disk full condition (`table is full`), or an incorrect username/password in the application's database configuration (`access denied`).
**Alert Expression:**
```logql
sum(count_over_time(
  {platform="mysql"}
  |~ `(?i)(error|slow\s*query|lock\s+time|table.*is\s+full|access\s+denied)`
  [5m])) > 0
```
**Remediation Steps:**
1. Query Loki for the full MySQL log entry: `{platform="mysql"} |~ "slow query|error"`.
2. For slow queries: extract the query from the slow log and run `EXPLAIN` against the production database.
3. Check for lock contention: `SHOW ENGINE INNODB STATUS\G` via SSH (read-only diagnostic).
4. For `table is full`: check disk space on the remote server: `df -h`.
5. For `access denied`: verify the application's database credentials in its config file.
6. Check the LCP correlation dashboard — `LcpCorrelation_SlowDbQuery` may also be firing, providing the specific queries.
**Escalate If:** `access denied` errors are seen (credential rotation required); disk is full (requires immediate disk cleanup or expansion); lock contention causes a database freeze (requires DBA intervention with `KILL` statements).

---

### LcpDegraded_ServerHealthy
**Severity:** P2
**Source:** Prometheus metrics
**Stabilization Window:** for: 2m
**Auto-Remediated:** No
**Triggers When:** The maximum LCP value exceeds 4000ms AND CPU utilization on the remote server is below 85%, both sustained for 2 minutes. This is the complement of `LcpDegraded_CpuBottleneck` — LCP is degraded but infrastructure metrics are within normal range.
**Business Impact:** Users are experiencing poor LCP (above 4 seconds) but the server itself appears healthy. This pattern points to an application-layer issue: a recent code deployment, a third-party script slowing the frontend, CDN misconfiguration, or a large image/asset degrading rendering performance. Since the root cause is not infrastructure, auto-remediation cannot help.
**Root Cause:** A JavaScript file added by a recent deployment that blocks rendering, a slow-loading third-party analytics or chat widget, a CSS file that was accidentally un-cached, large unoptimized hero images, a broken CDN configuration causing assets to load from origin, or application-level logic added to a critical rendering path.
**Alert Expression:**
```promql
(max(probe_lcp_ms) > 4000)
and on()
((100 - (avg(rate(node_cpu_seconds_total{mode="idle",instance_name="dev-regenics"}[5m])) * 100)) < 85)
```
**Remediation Steps:**
1. Check the correlation dashboard: `http://localhost:3000/d/lcp-correlation`.
2. Correlate the start of the LCP degradation with recent git deployments.
3. Run a manual Lighthouse audit on the affected page to identify specific slow resources.
4. Check if Loki correlation rules (`LcpCorrelation_WorkerPoolExhausted`, `LcpCorrelation_SlowDbQuery`) are also firing — if they are, the server may not be as healthy as metrics suggest.
5. Check CDN/cache headers: are assets being served with proper `Cache-Control` headers?
6. Review third-party scripts: check network waterfalls for any new render-blocking resources.
**Escalate If:** LCP degradation persists for more than 30 minutes without an identified cause; a business-critical page (homepage, product page, checkout) is affected; the degradation correlates with a deployment that requires a rollback decision.

---

### TargetDown
**Severity:** P2
**Source:** Prometheus metrics
**Stabilization Window:** for: 2m
**Auto-Remediated:** No
**Triggers When:** Prometheus's `up` metric is `0` for any scrape target (any job/instance combination), sustained for 2 minutes. This fires when Prometheus cannot successfully scrape a target — the exporter is down, the network is unreachable, or the target service has crashed.
**Business Impact:** The monitoring system has lost visibility into one or more components. While the alert itself does not indicate a user-facing outage, a blind spot in monitoring means other alerts may not fire if the downed exporter was providing the metrics they depend on. For example, if the Node Exporter on 10.10.2.21 is down, no CPU/memory/disk P3 alerts will fire.
**Root Cause:** The exporter process crashed or was stopped (Node Exporter, Nginx Exporter, or Promtail on 10.10.2.21), a network partition between 10.10.2.77 and 10.10.2.21, the remote server itself is down, a Docker container on 10.10.2.77 has crashed (Prometheus, Loki, Pushgateway, cAdvisor, Node Exporter), or a port conflict caused an exporter to fail to bind.
**Alert Expression:**
```promql
up == 0
```
**Remediation Steps:**
1. Note the `job` and `instance` labels on the alert to identify which target is down.
2. If it is a remote target (10.10.2.21): SSH to the server and check `systemctl status node-exporter` / `systemctl status promtail`.
3. If it is a Docker container (on 10.10.2.77): `docker ps -a` — check if the container has exited. `docker logs <container>` for the reason.
4. If it is a network issue: `ping 10.10.2.21` from 10.10.2.77; check if the LAN is healthy.
5. Restart the downed service: `systemctl restart <service>` or `docker-compose restart <service>`.
**Escalate If:** The remote server (10.10.2.21) is completely unreachable; multiple targets go down simultaneously (indicates a network or host-level failure); a Docker container restart loop is identified (`docker logs` showing repeated crashes).

---

### AlertmanagerNotificationFailing
**Severity:** P2
**Source:** Prometheus metrics
**Stabilization Window:** for: 5m
**Auto-Remediated:** No
**Triggers When:** The rate of `alertmanager_notifications_failed_total` is greater than zero, sustained for 5 minutes. This counter increments whenever Alertmanager fails to deliver a notification to any configured receiver — email, webhook, or Slack.
**Business Impact:** Alert notifications are not reaching their destinations. Engineers are not receiving emails for active incidents. Auto-remediation may also be silently failing if the webhook receiver is not receiving posts. The monitoring system is operational but blind from a notification perspective.
**Root Cause:** Gmail SMTP app password has expired or been revoked, SMTP host is unreachable (internet connectivity issue on the monitoring laptop), the webhook service on :5051 has crashed (gunicorn/systemd service failure), or the Alertmanager configuration file contains an invalid receiver (e.g., a template rendering error).
**Alert Expression:**
```promql
rate(alertmanager_notifications_failed_total[5m]) > 0
```
**Remediation Steps:**
1. Check Alertmanager logs: `docker logs alertmanager --tail 50`.
2. Identify which receiver is failing: the log will show `receiver=<name>` in the error.
3. For email failures: test SMTP manually from the monitoring machine — `python3 test_email.py`.
4. For webhook failures: check `systemctl status auto-remediation-webhook` on 10.10.2.77.
5. Verify the webhook is responding: `curl -s http://127.0.0.1:5051/health`.
6. If the Gmail app password has expired: generate a new one in Google Account → Security → App Passwords, update `config.json`, run `python3 generate_configs.py`, and reload Alertmanager.
**Escalate If:** SMTP failures indicate the Gmail account itself is locked or suspended; the webhook service cannot be restarted; Alertmanager is producing configuration errors after a recent config change.

---

## P3 Low Alerts

P3 alerts represent infrastructure trends that require attention but not immediate response. They are sent with a 24-hour repeat interval and may be batched into daily digest emails.

---

### P3_DiskSpaceLow
**Severity:** P3
**Source:** Prometheus metrics
**Stabilization Window:** for: 30m
**Auto-Remediated:** No
**Triggers When:** The available bytes on the root filesystem (`/`) of the remote server (10.10.2.21) drop below 20% of total size, sustained for 30 minutes. Computed from `node_filesystem_avail_bytes` and `node_filesystem_size_bytes` from the Node Exporter.
**Business Impact:** At 20% free disk space, the server has time to plan and act before a crisis. If disk fills completely, MySQL will stop accepting writes (InnoDB cannot write redo logs), PHP-FPM will fail to write session files, and nginx will fail to write access logs (potentially crashing log rotation). Log-intensive operations (database, application logs) accelerate disk consumption rapidly.
**Root Cause:** Application logs not rotating (logrotate misconfigured), MySQL binary logs growing unbounded, WordPress media uploads consuming space, Magento cache directory bloat, old PHP session files accumulating in `/var/lib/php/sessions`, or Docker log files growing without size limits.
**Alert Expression:**
```promql
(node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) < 0.20
```
**Remediation Steps:**
1. SSH to 10.10.2.21 and run `df -h` to confirm disk usage.
2. Identify the largest consumers: `du -sh /* 2>/dev/null | sort -h | tail -20`.
3. Check log directories: `du -sh /var/log/*`.
4. Run log rotation manually: `logrotate -f /etc/logrotate.conf`.
5. Clear old MySQL binary logs: `PURGE BINARY LOGS BEFORE DATE_SUB(NOW(), INTERVAL 3 DAY)`.
6. Clear old PHP sessions: `find /var/lib/php/sessions -mtime +7 -delete`.
7. Check `/var/log/cwv/` — the Playwright vitals log has no automatic rotation by default.
**Escalate If:** Disk drops below 10% (upgrade severity to immediate); database data directory is consuming the majority of space (requires database maintenance); disk cannot be freed without deleting application files.

---

### P3_HighMemoryUsage
**Severity:** P3
**Source:** Prometheus metrics
**Stabilization Window:** for: 30m
**Auto-Remediated:** No
**Triggers When:** The ratio of used memory to total memory on the remote server exceeds 85% (computed as `1 - MemAvailable / MemTotal` from Node Exporter), sustained for 30 minutes. Note: this uses `MemAvailable` (which accounts for kernel buffer reclaim) rather than `MemFree`, giving a more accurate view of actual memory pressure.
**Business Impact:** High memory pressure degrades application performance through increased page swapping, slower memory allocation for PHP processes, and potential Linux OOM killer activation which could kill MySQL, PHP-FPM, or Redis processes without warning.
**Root Cause:** PHP-FPM workers accumulating memory over time (memory leaks in application code), Redis growing beyond expected bounds, MySQL's InnoDB buffer pool sized too large for the available memory, a runaway PHP script that allocated a large amount of memory without releasing it, or a traffic spike that created more simultaneous PHP worker processes than expected.
**Alert Expression:**
```promql
(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) > 0.85
```
**Remediation Steps:**
1. SSH to 10.10.2.21: `free -h` and `vmstat -s | head -10` to understand current usage.
2. Identify memory consumers: `ps aux --sort=-%mem | head -20`.
3. Check if Redis is using its expected memory: `redis-cli info memory`.
4. Check MySQL InnoDB buffer pool: `mysql -e "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';"`.
5. Check PHP-FPM per-process memory: PHP-FPM worker processes should not exceed the configured memory limit.
6. If memory is under sustained pressure, consider reloading PHP-FPM to recycle workers (`systemctl reload php8.4-fpm`).
**Escalate If:** Memory exceeds 95%; the OOM killer has already activated (check `dmesg | grep -i "oom"`); MySQL or Redis have been killed by OOM.

---

### P3_HighCPUUsage
**Severity:** P3
**Source:** Prometheus metrics
**Stabilization Window:** for: 30m
**Auto-Remediated:** No
**Triggers When:** The average CPU utilization (across all cores) exceeds 85%, computed as `100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)`, sustained for 30 minutes. Note: this is an average across all CPUs, so a single-core spike on a multi-core machine may not trigger this alert.
**Business Impact:** Sustained high CPU over 30 minutes indicates a structural load problem rather than a transient spike. PHP request processing is slower, causing higher response times across the board. Background jobs compete with web request handlers for CPU time. If CPU reaches 100%, new PHP-FPM workers will be slow to start and request queuing begins.
**Root Cause:** Scheduled background jobs (WordPress cron, Magento cron, database maintenance jobs), a traffic surge beyond normal capacity, a search-engine crawl consuming excessive resources, PHP opcode cache (OPcache) being invalidated causing recompilation overhead, or a runaway process triggered by a code change.
**Alert Expression:**
```promql
100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 85
```
**Remediation Steps:**
1. SSH to 10.10.2.21: `top -b -n1` or `htop` to identify CPU-consuming processes.
2. Check if cron jobs are running during peak hours: `cat /etc/cron*` and check systemd timers.
3. Check for active PHP-FPM workers and what they are processing.
4. Review nginx access logs in Loki for unusual traffic patterns (bot crawls, scraping).
5. If a cron job is the cause: reschedule it to off-peak hours.
6. If traffic is legitimately high: check `P2_HighRequestLatency` for user impact and consider capacity planning.
**Escalate If:** CPU stays above 95% for more than 5 minutes (upgrade to P1-equivalent response); a specific process cannot be identified as the cause; the load is caused by a security incident (DDoS, crypto mining).

---

### P3_PlatformWarningRateElevated
**Severity:** P3
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** Yes (generic_triage.sh — collects triage data only)
**Triggers When:** At least one log line matching `warning`, `notice`, or `deprecated` (case-insensitive) appears in any platform stream (`nginx`, `php-fpm`, `mysql`, `wordpress`, `laravel`) within the last 5 minutes. The `platform` label identifies which application generated the warning.
**Business Impact:** Warnings do not directly impact users but indicate code quality issues, deprecated API usage, or configuration problems that will eventually become errors. PHP deprecation notices often predict PHP version upgrade failures. MySQL warnings can precede constraint violations. These should be reviewed in a daily digest rather than causing immediate interruption.
**Root Cause:** Application code using deprecated PHP functions (`strtotime()` called with invalid input, `each()` deprecated since PHP 7.2), WordPress plugin using outdated APIs, MySQL warnings about implicit type conversion or truncated data, nginx warnings about deprecated directives, or Magento raising notices about missing translations.
**Alert Expression:**
```logql
sum by (platform) (count_over_time(
  {platform=~"nginx|php-fpm|mysql|wordpress|laravel"}
  |~ `(?i)(warning|notice|deprecated)`
  [5m])) > 0
```
**Remediation Steps:**
1. This alert is designed for daily review, not immediate action.
2. Query Loki for the warning messages: `{platform="<platform>"} |~ "warning|deprecated"` over the past 24 hours.
3. Group warnings by message pattern to identify the most frequent issues.
4. Create a backlog ticket for each distinct warning class.
5. Prioritize `deprecated` notices if a PHP version upgrade is planned.
**Escalate If:** Warning volume is unusually high (thousands per minute indicates a loop or a broken component); a warning is `mysql: access denied` or similar security-relevant message (escalate to P2).

---

## Correlation Alerts

Correlation alerts are produced by the LCP correlation engine — a two-track system combining Playwright-collected Core Web Vitals with infrastructure metrics and log patterns to provide root-cause-labeled alerts. They use the `root_cause` label to pre-diagnose the alert before it reaches the engineer.

---

### LcpCorrelation_WorkerPoolExhausted
**Severity:** P1
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** No (this is a correlation/root-cause labeling rule; `FPM_WorkerPoolExhaustion` handles the remediation)
**Triggers When:** PHP-FPM logs more than zero events matching `server reached pm.max_children` or `max_children` in the last 5 minutes. This is intentionally broader than the 1-minute window in `FPM_WorkerPoolExhaustion` to provide correlation context even for historical events within the LCP analysis window.
**Business Impact:** This alert provides the root-cause label `worker_pool_exhausted` for the LCP correlation dashboard. When LCP is also elevated, engineers immediately know that slow page loads are caused by PHP-FPM queue backup — not by a code or CDN issue. Without this correlation, engineers would need to manually cross-reference logs and metrics.
**Root Cause:** See `FPM_WorkerPoolExhaustion`. The signals here are the same — PHP-FPM pool exhaustion — but this rule exists for correlation dashboard context and the `root_cause_detail` annotation: "PHP-FPM hit pm.max_children — new requests are queuing and timing out."
**Alert Expression:**
```logql
sum(count_over_time({platform="php-fpm"} |~ "(?i)(server reached pm.max_children|max_children)" [5m])) > 0
```
**Remediation Steps:**
1. Refer to `FPM_WorkerPoolExhaustion` remediation steps — that alert handles the auto-remediation.
2. Check the LCP correlation dashboard to see if LCP degradation correlates with this event.
3. Review pm.max_children setting: `cat /etc/php/8.4/fpm/pool.d/www.conf | grep max_children`.
4. Check pm.status page (if enabled): `nginx location /status` for real-time pool stats.
**Escalate If:** Pool exhaustion is recurring (multiple events per day) — this indicates a capacity or performance optimization issue requiring code review or infrastructure scaling.

---

### LcpCorrelation_SlowDbQuery
**Severity:** P1
**Source:** Loki logs
**Stabilization Window:** for: 0m (immediate)
**Auto-Remediated:** No (root-cause labeling rule)
**Triggers When:** More than 3 MySQL log events matching slow query or lock time patterns (`slow query`, `Query_time`, `Lock_time`, `lock time`, `table is full`) appear in the `mysql` platform stream within the last 5 minutes. The threshold of `> 3` (rather than `> 0`) reduces noise from occasional slow queries and fires only when there is a pattern.
**Business Impact:** Provides the root-cause label `slow_db_query` for correlation with LCP degradation. When both this alert and an LCP degradation alert are firing simultaneously, the diagnosis is clear: slow database queries are causing PHP workers to block, which causes request queuing, which causes high LCP. The LCP correlation dashboard shows the slow query log panel linked directly from this alert's `slow_query_log` annotation.
**Root Cause:** Missing database index on a frequently-queried column, a complex JOIN query without proper indexing, InnoDB row-level locking causing queue-up (high lock time), a table that has grown to the point where a previously-fast full-scan is now slow, or N+1 query patterns in ORM code (Eloquent, WP_Query).
**Alert Expression:**
```logql
sum(count_over_time({platform="mysql"} |~ "(?i)(slow\\s*query|Query_time|Lock_time|lock\\s+time|table.*is\\s+full)" [5m])) > 3
```
**Remediation Steps:**
1. Open the slow query log panel in the LCP correlation dashboard.
2. Identify the top offending queries by `Query_time` and `Lock_time`.
3. Run `EXPLAIN` on each offending query to identify missing indexes.
4. Add appropriate indexes: `CREATE INDEX idx_name ON table(column)`.
5. For lock contention: review the transaction isolation level and query patterns for conflicting writes.
6. For `table is full`: check disk space and MySQL's `tmp_table_size` and `max_heap_table_size` settings.
**Escalate If:** A query requires a schema migration to fix; lock contention requires killing active database connections; the slow query is in third-party plugin code that cannot be easily modified.

---

### MonitoringDiskLow
**Severity:** P2
**Source:** Prometheus metrics
**Stabilization Window:** for: 15m
**Auto-Remediated:** No
**Triggers When:** The root filesystem available space on the monitoring machine (10.10.2.77) drops below 15%, filtered to `job="node-exporter-local"` to distinguish from the remote server's disk alert (`P3_DiskSpaceLow`). The threshold is lower (15% vs 20%) because data loss from the monitoring machine is more catastrophic than from the application server.
**Business Impact:** If the monitoring laptop's disk fills, Prometheus will stop writing new samples (TSDB blocks cannot be created), Loki will stop ingesting logs (no space to write chunks), and Grafana will lose its state database. The monitoring system will go blind. Docker itself may also become unstable if `/var/lib/docker` fills. Critically, this is a P2 alert because losing monitoring is a significant operational risk even if users are not directly affected.
**Root Cause:** Prometheus TSDB blocks accumulating faster than the 15-day retention compaction can remove them, Loki log chunks growing rapidly due to high log volume from the application server, Docker image/volume accumulation, or the monitoring laptop being used for other purposes that fill disk.
**Alert Expression:**
```promql
(
  node_filesystem_avail_bytes{job="node-exporter-local", mountpoint="/"}
  / node_filesystem_size_bytes{job="node-exporter-local", mountpoint="/"}
) < 0.15
```
**Remediation Steps:**
1. Run `df -h` on 10.10.2.77 to confirm current disk usage.
2. Check Docker disk usage: `docker system df` — identifies unused images, stopped containers, and dangling volumes.
3. Clean Docker resources: `docker system prune --volumes` (WARNING: removes stopped containers and unused volumes).
4. Check Prometheus TSDB size: `du -sh /var/lib/docker/volumes/aiops-stack_prometheus-data`.
5. Check Loki data size: `du -sh /var/lib/docker/volumes/aiops-stack_loki-data`.
6. If Prometheus is the primary consumer, consider reducing retention: `--storage.tsdb.retention.time=7d` temporarily.
7. If Loki is the primary consumer, check if the remote server is generating unusually high log volume.
**Escalate If:** Disk drops below 5%; Prometheus or Loki containers crash due to disk-full errors; Docker daemon becomes unresponsive.

---

### PrometheusScrapeFailing
**Severity:** P3
**Source:** Prometheus metrics
**Stabilization Window:** for: 5m
**Auto-Remediated:** No
**Triggers When:** Either `prometheus_target_scrape_pool_exceeded_target_limit_total` has a positive rate (scrape pool limits exceeded) OR `scrape_duration_seconds` rate exceeds 10 seconds (scrapes taking longer than 10 seconds), sustained for 5 minutes. This indicates Prometheus is struggling to collect metrics effectively rather than individual targets being down (which is covered by `TargetDown`).
**Business Impact:** Prometheus metrics are incomplete or delayed. Metric-based alerts (P1_5xxErrorRateCritical, PHPFPMWorkerPoolExhaustion, latency alerts) may fail to fire or fire with outdated data. The monitoring system is operationally degraded. Note: `TargetDown` would fire separately if specific targets are unreachable.
**Root Cause:** Prometheus scrape pool configured with too-low target limits, an exporter on the remote server responding very slowly (high latency in metric collection), Prometheus CPU or memory constraints causing slow internal processing, an exporter generating an extremely large metrics payload (cardinality explosion from a high-cardinality label like URI without query-string stripping), or a network-level latency spike between 10.10.2.77 and 10.10.2.21.
**Alert Expression:**
```promql
rate(prometheus_target_scrape_pool_exceeded_target_limit_total[5m]) > 0
or
rate(scrape_duration_seconds[5m]) > 10
```
**Remediation Steps:**
1. Check Prometheus logs: `docker logs prometheus --tail 50`.
2. Check scrape durations in the Prometheus UI: navigate to `http://127.0.0.1:9090/targets` and review the "Last Scrape Duration" column.
3. Identify the slow scrape target and investigate that exporter's performance.
4. Check for cardinality issues: `http://127.0.0.1:9090/tsdb-status` — look for series with unusually high cardinality.
5. If Promtail metrics are the source of cardinality explosion, verify the URI query-string stripping stage is active in `promtail-config.yml`.
6. Check Prometheus resource usage: `docker stats prometheus`.
**Escalate If:** Prometheus is consuming excessive memory (OOM kill risk); a cardinality explosion is identified that requires rule changes to fix; scrape durations exceed 60 seconds (Prometheus will begin dropping scrapes).

---

## Summary Table

| Alert Name | Severity | Source | Auto-Remediated | Repeat Interval | Business Impact |
|---|---|---|---|---|---|
| P1_5xxErrorRateCritical | P1 | Prometheus | No (generic_triage) | 1h | Errors |
| P1_5xxCountSpike | P1 | Prometheus | No (generic_triage) | 1h | Errors |
| PHPFPMWorkerPoolExhaustion | P1 | Prometheus | Yes (generic_triage) | 1h | Unavailable |
| HighHTTP5xxRate | P1 | Prometheus | Yes (generic_triage) | 1h | Errors |
| FPM_WorkerPoolExhaustion | P1 | Loki | Yes (fpm-reload) | 1h | Unavailable |
| Nginx_TooManyOpenFiles | P1 | Loki | Yes (nginx-file-limit) | 1h | Unavailable |
| Redis_Memory_High | P1 | Loki | Yes (redis-flush-cache) | 1h | Writes fail |
| P1_CheckoutPaymentFatal | P1 | Loki | Yes (generic_triage) | 1h | Revenue loss |
| LcpDegraded_CpuBottleneck | P1 | Prometheus | No | 1h | Slow |
| P2_HighRequestLatency | P2 | Prometheus | No | 4h | Slow |
| P2_SlowURLLatency | P2 | Prometheus | No | 4h | Slow |
| P2_UpstreamConnectionErrors | P2 | Prometheus | No | 4h | Errors |
| P2_AppFatalRateHigh | P2 | Loki | Yes (generic_triage) | 4h | Errors |
| P2_MySQLIssuesHigh | P2 | Loki | Yes (generic_triage) | 4h | Degraded |
| LcpDegraded_ServerHealthy | P2 | Prometheus | No | 4h | Slow |
| TargetDown | P2 | Prometheus | No | 4h | Blind spot |
| AlertmanagerNotificationFailing | P2 | Prometheus | No | 4h | Silent |
| P3_DiskSpaceLow | P3 | Prometheus | No | 24h | Risk |
| P3_HighMemoryUsage | P3 | Prometheus | No | 24h | Risk |
| P3_HighCPUUsage | P3 | Prometheus | No | 24h | Degraded |
| P3_PlatformWarningRateElevated | P3 | Loki | Yes (generic_triage) | 24h | Quality |
| LcpCorrelation_WorkerPoolExhausted | P1 | Loki | No | 1h | Correlation |
| LcpCorrelation_SlowDbQuery | P1 | Loki | No | 1h | Correlation |
| MonitoringDiskLow | P2 | Prometheus | No | 4h | Blind spot |
| PrometheusScrapeFailing | P3 | Prometheus | No | 24h | Blind spot |
