# Technical Architecture — AIOps Platform

---

## Overview

This platform is an AIOps-oriented observability and auto-remediation system built for production web infrastructure running Laravel, WordPress, and Magento workloads under a shared Nginx/PHP-FPM/MySQL/Redis stack. The system captures two independent signal channels — time-series metrics (Prometheus) and structured logs (Loki) — and fuses them into a unified alerting pipeline that routes through Alertmanager. When an alert carries a `remediation` label, the pipeline continues through a Python Flask webhook that validates the request, selects a bash remediation script from a strict allowlist, and executes the corrective action on the remote application server over SSH — completing a closed-loop feedback cycle from symptom detection to automated resolution without human intervention.

The design prioritizes root-cause isolation over reactive monitoring. Rather than surfacing every threshold breach as an undifferentiated alert, the system implements a three-tier severity model (P1/P2/P3) coupled with an LCP correlation engine that fuses Core Web Vitals data (collected by a Playwright cron and pushed via Pushgateway) with infrastructure metrics and application log patterns. This enables the system to distinguish between, for example, a user-visible LCP degradation caused by a CPU-saturated server versus one caused by a recent application deployment — producing actionable, root-cause-labeled alerts instead of raw threshold notifications.

---

## Design Principles

- **Dual-signal alerting (metrics AND logs):** Prometheus metrics capture numeric trends such as error rates, latency percentiles, and resource utilization with high-resolution time-series fidelity. Loki log rules capture semantic events such as "PHP-FPM reached max_children" or "Redis OOM command not allowed" which either appear too infrequently to form stable rates or only exist as textual log lines. Using both signal sources independently — each with its own rule evaluator — ensures that fast transient events (detected by Loki's zero-`for` rules) and sustained trend degradations (detected by Prometheus's multi-minute stabilization windows) are both covered without compromise.

- **Config-driven single source of truth:** All environment-specific values (IP addresses, ports, credentials, platform names) live exclusively in `config.json`. A Python generator script (`generate_configs.py`) derives every other configuration file — `.env`, `prometheus.yml`, `alertmanager.yml`, `promtail-config.yml` — from that single file using template substitution. This eliminates the class of misconfiguration bugs that arise from editing derived files directly and ensures that adding a new environment requires editing only one file.

- **SSH-over-agent for remote execution:** Remediation actions execute on the remote application server (10.10.2.21) via paramiko SSH rather than through a resident agent or sidecar process. This approach requires zero software installation on the monitored server beyond standard SSH access, keeps the attack surface minimal (no persistent listening process), and allows the remediation engine to be upgraded or replaced entirely without touching the application server. The trade-off is slightly higher latency per action (SSH handshake overhead), which is acceptable for remediation workloads that run in the seconds-to-minutes range.

- **Local storage with bounded retention:** All telemetry data is stored on the monitoring laptop's local filesystem within named Docker volumes. Prometheus retains 15 days of metrics; Loki retains 90 days of logs. This choice eliminates cloud egress costs and external service dependencies, keeps the system operational during internet outages, and ensures that sensitive log data (containing stack traces, database queries, and user activity patterns) never leaves the LAN. The acknowledged trade-off is the monitoring laptop becoming a single point of failure for the observability layer.

- **Three-tier severity with separate notification channels:** Alerts are classified as P1 (critical, page immediately), P2 (high, respond within one hour), or P3 (low, include in daily digest). Each tier has a distinct Alertmanager receiver with its own email recipient list and repeat interval. This prevents alert fatigue by ensuring that disk-space warnings do not share a notification channel with payment-flow crashes, and ensures that on-call engineers receive the right level of interruption for the right type of event.

---

## Full System Architecture Diagram

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║  REMOTE APPLICATION SERVER — 10.10.2.21                                        ║
║                                                                                  ║
║  ┌─────────────┐  ┌─────────────┐  ┌──────────┐  ┌───────────────────────────┐ ║
║  │    Nginx    │  │  PHP-FPM    │  │  MySQL   │  │  Redis                    │ ║
║  │ :80/:443    │  │ php8.4-fpm  │  │  :3306   │  │  :6379                    │ ║
║  └──────┬──────┘  └──────┬──────┘  └────┬─────┘  └────────────┬──────────────┘ ║
║         │ access.log      │ php-fpm.log  │ error.log           │                ║
║         │ error.log       │              │ slow.log            │                ║
║         ▼                 ▼              ▼                     │                ║
║  ┌──────────────────────────────────────────────────────────┐  │                ║
║  │  Promtail :9080  (systemd)                               │  │                ║
║  │  5 jobs: nginx-access(JSON) nginx-error php-fpm          │  │                ║
║  │           mysql  platform-apps(Laravel/WP/Magento)       │  │                ║
║  │  + job: cwv-rum → /var/log/cwv/vitals.log                │  │                ║
║  │                                                          │  │                ║
║  │  nginx-access pipeline:                                  │  │                ║
║  │    JSON parse → s-to-ms conversion → URI strip           │  │                ║
║  │    → label promote → emit Histogram metrics              │  │                ║
║  └──────────────────────────┬───────────────────────────────┘  │                ║
║                             │ HTTP POST /loki/api/v1/push       │                ║
║  ┌──────────────────────┐   │                                   │                ║
║  │ Node Exporter :9100  │   │   ┌──────────────────────────┐   │                ║
║  │ (systemd)            │   │   │  Playwright cron          │   │                ║
║  │ CPU/mem/disk/net     │   │   │  (every 3 min)            │   │                ║
║  └──────────────────────┘   │   │  Core Web Vitals →        │   │                ║
║                             │   │  /var/log/cwv/vitals.log  │   │                ║
║  ┌──────────────────────┐   │   └──────────────────────────┘   │                ║
║  │ Nginx Exporter :9113 │   │                                   │                ║
║  │ (systemd)            │   │   ┌──────────────────────────┐   │                ║
║  │ nginx_http_requests  │   │   │  CrowdSec                 │   │                ║
║  └──────────────────────┘   │   │  (IDS/IPS, LAN access)   │   │                ║
║                             │   └──────────────────────────┘   │                ║
╚═════════════════════════════╪═══════════════════════════════════╪════════════════╝
                              │ LAN 10.10.2.0/24                  │
    HTTP scrape :9100 ────────┤          ┌────────────────────────┘
    HTTP scrape :9113 ────────┤          │ HTTP push (CWV metrics)
    SSH exec :22    ──────────┤          │ to Pushgateway :9091
                              │          │
╔═════════════════════════════╪══════════╪══════════════════════════════════════════╗
║  MONITORING LAPTOP — 10.10.2.77        │                                         ║
║                                        │                                         ║
║  ┌─── Docker network: aiops-network 172.20.0.0/16 ────────────────────────────┐ ║
║  │                                                                             │ ║
║  │  ┌──────────────────────────────────────────────────────────────────────┐  │ ║
║  │  │  LOKI :3100  (grafana/loki:2.9.0)                                    │  │ ║
║  │  │  Bound: LOCAL_IP:3100  (LAN-accessible for Promtail push)            │  │ ║
║  │  │  Storage: loki-data volume                                           │  │ ║
║  │  │  Schema: v11/boltdb-shipper (pre-2026-05-02) → v12/tsdb (current)    │  │ ║
║  │  │  Retention: 90 days  |  Ruler: evaluates LogQL alerts every 15s      │  │ ║
║  │  │  Sends alerts → Alertmanager :9093                                   │  │ ║
║  │  └──────────────────────┬───────────────────────────────────────────────┘  │ ║
║  │                         │ service_healthy (healthcheck)                    │ ║
║  │                         ▼                                                  │ ║
║  │  ┌──────────────────────────────────────────────────────────────────────┐  │ ║
║  │  │  PROMETHEUS :9090  (prom/prometheus:v2.51.0)                         │  │ ║
║  │  │  Bound: 127.0.0.1:9090                                               │  │ ║
║  │  │  Scrapes: remote :9100, :9113  |  local :9100, :8080, :9091          │  │ ║
║  │  │  Storage: prometheus-data volume  |  Retention: 15 days              │  │ ║
║  │  │  Rules: app-alerts.yaml, correlation-alerts.yaml, self-monitoring    │  │ ║
║  │  │  Eval interval: 15s (P1/auto-remediation), 30s (P2), 1m (P3)        │  │ ║
║  │  │  Sends alerts → Alertmanager :9093                                   │  │ ║
║  │  └──────────────────────┬───────────────────────────────────────────────┘  │ ║
║  │                         │                                                  │ ║
║  │  ┌──────────────────────▼───────────────────────────────────────────────┐  │ ║
║  │  │  ALERTMANAGER :9093  (prom/alertmanager:v0.27.0)                     │  │ ║
║  │  │  Bound: 127.0.0.1:9093                                               │  │ ║
║  │  │  Receives from: Prometheus + Loki ruler                              │  │ ║
║  │  │  Routes: P1 → email oncall  |  P2 → email team  |  P3 → daily       │  │ ║
║  │  │  Remediation route → webhook :5051 (Bearer token)                   │  │ ║
║  │  │  Storage: alertmanager-data volume  |  Dedup via grouping            │  │ ║
║  │  └──────┬─────────────────────────┬────────────────────────────────────┘  │ ║
║  │         │ email (SMTP/TLS)         │ HTTP POST /webhook (Bearer token)     │ ║
║  │         ▼                         ▼                                        │ ║
║  │  ┌─────────────┐    ┌────────────────────────────────────────────────────┐ │ ║
║  │  │  Gmail SMTP │    │  WEBHOOK :5051  (Flask/Gunicorn, systemd service)  │ │ ║
║  │  │  (external) │    │  webhook.py  |  NOT in Docker                      │ │ ║
║  │  └─────────────┘    │  ALLOWED_SCRIPTS: fpm-reload, nginx-file-limit,    │ │ ║
║  │                     │                   redis-flush-cache, generic_triage │ │ ║
║  │                     │  Dedup window: 30s  |  Request cap: 1 MB           │ │ ║
║  │                     │  Per-alert thread → bash script → ssh_helper.py   │ │ ║
║  │                     └────────────────────────────────────────────────────┘ │ ║
║  │                                                                             │ ║
║  │  ┌──────────────────────┐  ┌─────────────────────┐  ┌───────────────────┐  │ ║
║  │  │  GRAFANA :3000        │  │  PUSHGATEWAY :9091  │  │  NODE EXPORTER    │  │ ║
║  │  │  grafana-oss:10.4.0  │  │  pushgateway:v1.8.0 │  │  :9100 (local)    │  │ ║
║  │  │  Bound: LOCAL_IP     │  │  Bound: LOCAL_IP     │  │  127.0.0.1:9100   │  │ ║
║  │  │  Auth required       │  │  Receives CWV push  │  │  host metrics     │  │ ║
║  │  │  Datasources: Prom   │  │  probe_lcp_ms etc.  │  │  prom/node:v1.7.0 │  │ ║
║  │  │  + Loki              │  └─────────────────────┘  └───────────────────┘  │ ║
║  │  └──────────────────────┘                                                   │ ║
║  │                                                                             │ ║
║  │  ┌──────────────────────────────────────────────────────────────────────┐  │ ║
║  │  │  CADVISOR :8080  (cadvisor:v0.49.1)                                  │  │ ║
║  │  │  Bound: 127.0.0.1:8080  |  Per-container resource metrics            │  │ ║
║  │  └──────────────────────────────────────────────────────────────────────┘  │ ║
║  └─────────────────────────────────────────────────────────────────────────────┘ ║
║                                                                                  ║
║  Systemd (host):  auto-remediation-webhook.service  (gunicorn, :5051)           ║
╚══════════════════════════════════════════════════════════════════════════════════╝

Protocol Legend
  ──────►  HTTP scrape (Prometheus pull)
  - - - ►  HTTP push (Promtail→Loki, CWV→Pushgateway)
  ══════►  Alert POST (Prometheus/Loki→Alertmanager, Alertmanager→Webhook)
  ──SSH─►  SSH exec via paramiko (Webhook→Remote Server)
```

---

## Component Reference Table

| Name | Machine | Type | Port | Role | Version | Container/Service |
|---|---|---|---|---|---|---|
| Nginx | 10.10.2.21 | System | 80/443 | Web reverse proxy / static file server | - | systemd |
| PHP-FPM | 10.10.2.21 | System | unix socket | PHP process manager (www pool) | php8.4-fpm | systemd |
| MySQL | 10.10.2.21 | System | 3306 | Relational database | - | systemd |
| Redis | 10.10.2.21 | System | 6379 | Cache (DB 0) + session store (DB 1) | - | systemd |
| Node Exporter | 10.10.2.21 | Exporter | 9100 | Host CPU/memory/disk/network metrics | - | systemd |
| Nginx Exporter | 10.10.2.21 | Exporter | 9113 | nginx_http_requests_total metrics | - | systemd |
| Promtail | 10.10.2.21 | Log agent | 9080 | Log collection + pipeline + Loki push | - | systemd |
| Playwright cron | 10.10.2.21 | Cron | - | Core Web Vitals collection every 3 min → /var/log/cwv/vitals.log | - | cron |
| CrowdSec | 10.10.2.21 | Security | - | Intrusion detection/prevention | - | systemd |
| Loki | 10.10.2.77 | Log store | 3100 | Log aggregation, retention, ruler eval | 2.9.0 | Docker container |
| Prometheus | 10.10.2.77 | Metrics store | 9090 | Metrics scrape, storage, rule evaluation | v2.51.0 | Docker container |
| Alertmanager | 10.10.2.77 | Alert router | 9093 | Alert routing, dedup, notifications | v0.27.0 | Docker container |
| Grafana | 10.10.2.77 | Dashboard | 3000 | Visualization, datasource federation | 10.4.0 | Docker container |
| Pushgateway | 10.10.2.77 | Metrics gateway | 9091 | Receives pushed metrics (CWV/Playwright) | v1.8.0 | Docker container |
| Node Exporter | 10.10.2.77 | Exporter | 9100 | Local host metrics for monitoring machine | v1.7.0 | Docker container |
| cAdvisor | 10.10.2.77 | Exporter | 8080 | Per-container resource metrics | v0.49.1 | Docker container |
| Webhook / Gunicorn | 10.10.2.77 | Remediation engine | 5051 | Flask app — receives alerts, dispatches scripts | - | systemd (host) |

---

## Data Flows

### a. Metrics Collection Flow

```
Remote Server (10.10.2.21)                  Monitoring Laptop (10.10.2.77)
────────────────────────                    ──────────────────────────────

Node Exporter :9100 ─────────── HTTP GET /metrics ──────────────────► Prometheus
(CPU, mem, disk, net,            scrape interval: 15s                  (stores in
 filesystem, network)            job: node-exporter-remote             TSDB volume)

Nginx Exporter :9113 ─────────── HTTP GET /metrics ──────────────────►
(nginx_http_requests_total       scrape interval: 15s
 by status code, vhosts)         job: nginx-exporter

                                                                         │
Playwright cron                                                          │
  ↓ (every 3 min)                                                        │
/var/log/cwv/vitals.log          Promtail cwv-rum job                   │
  ↓ (JSON: metric, value,        parses JSON → labels                   │
     rating, page_type, url)     (metric_name, rating,                  │
                                  page_type, device)                     │
                                  ↓ HTTP POST →                          │
Pushgateway :9091 ◄──────────────── CWV push                            │
(probe_lcp_ms,                   (Playwright writes directly             │
 probe_cls, probe_fid)            to Pushgateway)                        │
  │                                                                      │
  └── HTTP GET /metrics ─────────────────────────────────────────────► Prometheus

Node Exporter (local) :9100 ─── HTTP GET /metrics ──────────────────►
(monitoring machine host         job: node-exporter-local
 metrics)

cAdvisor :8080 ───────────────── HTTP GET /metrics ──────────────────►
(per-container CPU, mem,         job: cadvisor
 net I/O for all containers)
```

### b. Log Collection Flow

```
Remote Server (10.10.2.21)
──────────────────────────

/var/log/nginx/*access.log (JSON structured)
  │  pipeline: JSON parse (status, uri, response_time_ms)
  │            template: seconds → milliseconds
  │            template: URI query-string strip (?... removed)
  │            labels: status, uri
  │            metrics: nginx_request_duration_ms Histogram (11 buckets)
  │                     nginx_upstream_duration_ms Histogram
  ▼
/var/log/nginx/*error.log (text)
  │  pipeline: regex timestamp → labels: level
  ▼
/var/log/php*-fpm.log (text)
  │  pipeline: regex timestamp, level extraction → labels: level
  ▼
/var/log/mysql/*.log (text)
  │  pipeline: regex timestamp, level extraction → labels: level
  ▼
/var/www/**/{storage/logs,wp-content,var/log}/*.log
  │  pipeline: regex timestamp → labels: level
  │  platforms: laravel, wordpress, magento
  ▼
/var/log/cwv/vitals.log (JSON)
     pipeline: JSON parse (metric_name, rating, page_type, device, url)
               → labels: metric_name, rating, page_type, device

All jobs → Promtail aggregates → HTTP POST /loki/api/v1/push
                                  ↓
                            Loki :3100 (LOCAL_IP:3100)
                            Ingester → WAL → chunks
                            Index: boltdb-shipper (old) / tsdb (new)
                            Compactor: 10 min interval, 90-day retention
                            Ruler: evaluates LogQL rules every 15s
                                   → fires to Alertmanager :9093
```

### c. Alert Flow

```
Prometheus                        Loki Ruler
rule eval (15s/30s/1m)           rule eval (15s)
       │                                 │
       │  HTTP POST /api/v2/alerts        │  HTTP POST /api/v2/alerts
       └────────────────┬────────────────┘
                        ▼
              Alertmanager :9093
              ┌──────────────────────────────────────────────────────────┐
              │  1. Deduplication (groupBy: alertname + platform)        │
              │  2. Inhibition rules (P1 suppresses P2/P3 same service)  │
              │  3. Route matching:                                       │
              │                                                           │
              │  match: severity=p1 ──────────────────────────────────► │
              │    receiver: email-p1-critical                            │
              │    group_wait: 30s  |  group_interval: 5m                │
              │    repeat_interval: 1h                                    │
              │                                                           │
              │  match: severity=p2 ──────────────────────────────────► │
              │    receiver: email-p2-high                                │
              │    repeat_interval: 4h                                    │
              │                                                           │
              │  match: severity=p3 ──────────────────────────────────► │
              │    receiver: email-p3-low                                 │
              │    repeat_interval: 24h                                   │
              │                                                           │
              │  match: remediation label present ───────────────────► │
              │    receiver: auto-remediation-webhook                     │
              │    HTTP POST http://127.0.0.1:5051/webhook               │
              │    Authorization: Bearer <secret_token>                   │
              └──────────────────────────────────────────────────────────┘
                   │                       │
                   ▼                       ▼
              Gmail SMTP            Webhook :5051
              (SMTP_SSL/465)        (see Remediation Flow)
              → email recipients
```

### d. Auto-Remediation Flow

```
Alertmanager
  │  HTTP POST /webhook
  │  Authorization: Bearer <token>
  │  Body: Alertmanager v2 JSON (alerts[], labels{}, status)
  ▼
Webhook Flask app (webhook.py)
  ├── Auth check: Bearer token == config.json webhook.secret_token  ──► 401 if mismatch
  ├── JSON decode  ──► 400 if invalid
  ├── Request size guard (1 MB cap)
  └── For each alert: spawn background thread (_process_alert)

_process_alert thread:
  ├── alert.status == "resolved"?
  │     └── _run_verify(task) → ssh_helper.py "systemctl status ..."
  │         → compose RCA summary email → _send_email → Gmail SMTP
  │         → return
  │
  ├── auto_remediate == false?  ──► log SKIPPED, return
  ├── dedup check: same fingerprint within 30s?  ──► silently return
  ├── allowlist check: task ∈ {fpm-reload, nginx-file-limit,
  │                             redis-flush-cache, generic_triage}?
  │     └── NO  ──► log BLOCKED, return
  ├── path traversal guard: realpath within remediate/  ──► reject if outside
  ├── script existence check  ──► log FAILED if missing
  │
  └── subprocess.run(["bash", script_path], env=labels_as_env, timeout=90s)
        │
        ▼
  Bash remediation script (e.g. fpm-reload.sh)
  ├── Read config.json for auto_remediate, remote_ip, credentials
  ├── Call: python3 remediate/ssh_helper.py "<systemctl command>"
  │
  ssh_helper.py (paramiko)
  ├── Load config.json → ip=10.10.2.21, user, password/sudo_password
  ├── paramiko.SSHClient().connect(10.10.2.21, timeout=15s)
  ├── exec_command("echo <sudo_pw> | sudo -S bash -c '<command>'")
  ├── collect stdout/stderr, recv_exit_status()
  └── return exit code to bash script
        │
        ▼
  Remote Server 10.10.2.21
  └── systemctl reload php8.4-fpm
      sysctl fs.file-max=100000 && systemctl reload nginx
      redis-cli -n <cache_db> FLUSHDB  (3 guards: auto_remediate,
                                         cache≠session, mem>90%)
        │
        ▼
  Script exit code → webhook logs SUCCESS/FAILED
  _store_history(fingerprint, execution_metadata)
  → wait for Alertmanager "resolved" firing
  → _run_verify() → post-remediation evidence
  → _send_email("[RESOLVED] alertname [platform]", RCA body)
```

---

## Configuration Architecture

```
config.json  (single source of truth — chmod 600, gitignored)
├── network:  local_ip, remote_ip
├── ports:    grafana, prometheus, loki, alertmanager,
│             pushgateway, webhook, node_exporter,
│             nginx_exporter, promtail, cadvisor
├── grafana:  admin_user, admin_password
├── email:    smtp_host, from_address, app_password,
│             recipients{p1, p2, p3, correlation}
├── ssh:      user, password, sudo_password
├── redis:    port, cache_db, session_db, password
├── slack:    webhook_url
├── webhook:  secret_token, auto_remediate
└── platform: name, instance_name, environment
       │
       ▼
python3 generate_configs.py
       │
       ├──► .env
       │    (docker-compose variable injection)
       │    LOCAL_IP, REMOTE_IP, GRAFANA_*, all PORT_* vars
       │    PLATFORM_NAME, PLATFORM_ENV
       │
       ├──► prometheus/prometheus.yml
       │    (from prometheus.yml.template)
       │    __LOCAL_IP__, __REMOTE_IP__, port placeholders,
       │    email/webhook settings
       │
       ├──► alertmanager/alertmanager.yml
       │    (from alertmanager.yml.template)
       │    SMTP credentials, recipient addresses,
       │    WEBHOOK_TOKEN, WEBHOOK_PORT
       │
       └──► promtail/promtail-config.yml
            (from promtail-config.yml.template)
            __LOCAL_IP__, __LOKI_PORT__, __PROMTAIL_PORT__,
            __PLATFORM_NAME__, __PLATFORM_ENV__

docker-compose.yml
  reads .env via ${VAR} substitution
  mounts prometheus.yml, alertmanager.yml as :ro volumes
  no secrets in docker-compose.yml itself
```

Validation: `python3 generate_configs.py --check` runs without writing files — validates that no `CHANGE_ME` placeholders remain and that `redis.cache_db != redis.session_db`.

---

## Promtail Pipeline Deep Dive

The `nginx-access` Promtail job is the most complex pipeline in the system and is responsible for producing the Histogram metrics that power P2 latency alerts and the LCP correlation engine.

**Input:** Nginx writes access logs in JSON format with fields including `status`, `uri`, and `response_time_ms` (stored in seconds as a float, e.g. `0.342`).

**Pipeline stages:**

```
Stage 1: json
  Extracts from the raw JSON log line:
    status        → HTTP status code string ("200", "500", etc.)
    uri           → raw request URI including query string
    raw_duration  → mapped from field "response_time_ms" (seconds float)
    raw_upstream  → upstream_response_time (seconds float or "-")

Stage 2: template (response_time_ms)
  Converts seconds to milliseconds:
    response_time_ms = raw_duration * 1000.0
  Uses Go template: {{ mulf (float64 .raw_duration) 1000.0 }}
  This is necessary because Nginx logs response_time in seconds
  but all downstream PromQL queries use milliseconds.

Stage 3: template (upstream_time_ms)
  Same conversion for upstream time, with guards:
    - skips if raw_upstream is empty string ""
    - skips if raw_upstream is "-" (Nginx uses "-" for direct responses)

Stage 4: template (uri — query string strip)
  Removes everything from "?" onward:
    uri = regexReplaceAll "\\?.*" .uri ""
  This prevents high-cardinality label explosion in the Histogram
  (each unique query string would create a new time series).

Stage 5: labels
  Promotes extracted values to Loki stream labels:
    status: (promotes to stream label for log querying)
    uri:    (promotes to stream label)

Stage 6: metrics
  Emits two Prometheus Histograms scraped by Prometheus from Promtail:

  promtail_custom_nginx_request_duration_ms:
    type: Histogram
    source: response_time_ms (milliseconds)
    buckets: [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
    labels: status, uri, platform, environment

  promtail_custom_nginx_upstream_duration_ms:
    type: Histogram
    source: upstream_time_ms (milliseconds)
    same buckets and labels
```

**Why this matters:** These Histogram metrics are the primary data source for `P2_HighRequestLatency`, `P2_SlowURLLatency`, and the LCP correlation Prometheus rules. The P95 latency PromQL expressions (`histogram_quantile(0.95, ...)`) depend entirely on this pipeline running correctly. A Promtail configuration error or nginx log format change will silently break all latency-based alerting.

---

## Alert Correlation System

The LCP correlation engine is a two-track system that identifies the root cause of a Core Web Vitals degradation (LCP > 4000ms) by joining it with other signal sources.

**Track 1: Prometheus-side correlation (compound PromQL with `and on()`)**

```promql
-- LcpDegraded_CpuBottleneck
(max(probe_lcp_ms) > 4000)
and on()
((100 - (avg(rate(node_cpu_seconds_total{mode="idle",instance_name="dev-regenics"}[5m])) * 100)) > 85)
```

The `and on()` operator with an empty label matcher performs a cross-series join — it fires only when BOTH the LCP scalar exceeds 4000ms AND the CPU scalar exceeds 85%, regardless of label alignment between the two different metric sources (Pushgateway vs. Node Exporter). This is necessary because `probe_lcp_ms` and `node_cpu_seconds_total` share no common labels.

```promql
-- LcpDegraded_ServerHealthy (infrastructure normal, check app)
(max(probe_lcp_ms) > 4000)
and on()
((100 - (avg(rate(node_cpu_seconds_total{mode="idle",...}[5m])) * 100)) < 85)
```

This fires when LCP is degraded but CPU is within normal range, routing the engineer toward application-layer investigation (recent deployment, external dependencies, CDN issues) rather than infrastructure.

**Track 2: Loki-side correlation (log-pattern root cause labeling)**

```logql
-- LcpCorrelation_WorkerPoolExhausted
sum(count_over_time({platform="php-fpm"} |~ "(?i)(server reached pm.max_children|max_children)" [5m])) > 0
```

```logql
-- LcpCorrelation_SlowDbQuery
sum(count_over_time({platform="mysql"} |~ "(?i)(slow\\s*query|Query_time|Lock_time|...)" [5m])) > 3
```

These Loki ruler rules fire when PHP-FPM or MySQL logs show patterns associated with LCP degradation. They run independently of the Prometheus-side rules — the Loki ruler evaluates them every 15 seconds and sends them directly to Alertmanager. The `root_cause` label on each alert (e.g., `root_cause: worker_pool_exhausted`, `root_cause: slow_db_query`) is included in the Alertmanager notification template to produce actionable, pre-diagnosed emails and dashboard links.

**Signal separation rationale:** CPU/memory correlations live in Prometheus because those signals are already there (Node Exporter). PHP-FPM and MySQL correlations live in Loki because the authoritative signals are the log lines themselves — Promtail's log pipeline is the system of record for those events, and log-pattern matching is more reliable than trying to infer PHP-FPM queue state from metric proxies.

---

## Security Architecture

**Layer 1: Network binding**
Services on the monitoring laptop are bound to specific interfaces. Security-sensitive services (Prometheus, Alertmanager) are bound to `127.0.0.1` — not accessible from the LAN. Loki, Pushgateway, and Grafana are bound to `LOCAL_IP` (10.10.2.77) to allow Promtail on the remote server to push data and for team dashboards. The webhook/gunicorn service listens on `0.0.0.0:5051` since Alertmanager calls it via Docker network; in production this should be restricted to `127.0.0.1` since Alertmanager runs on the same host.

**Layer 2: Authentication**
- Grafana: anonymous access explicitly disabled (`GF_AUTH_ANONYMOUS_ENABLED=false`). Admin credentials required.
- Webhook: Bearer token authentication on all `POST /webhook` requests. Token is stored in `config.json` and loaded at startup. A missing or mismatched token returns HTTP 401. Token is generated with `openssl rand -hex 32`.
- SSH: Credentials read from `config.json` at execution time by `ssh_helper.py`. SSH key-based auth is preferred; password is the fallback.

**Layer 3: Script allowlist**
The webhook enforces a `frozenset` allowlist of exactly four script names: `fpm-reload`, `nginx-file-limit`, `redis-flush-cache`, `generic_triage`. The `remediation` alert label value is checked against this set before any script is executed. After the allowlist check, a `realpath()` guard verifies that the resolved script path begins with the `remediate/` directory absolute path — preventing path traversal attacks where a crafted label value like `../../etc/passwd` could escape the directory.

**Layer 4: Credential storage**
`config.json` is `chmod 600` and added to `.gitignore`. It is never committed to version control. `config.example.json` is the committed template with `CHANGE_ME` placeholders. All derived files (`.env`, `prometheus.yml`, `alertmanager.yml`, `promtail-config.yml`) are also gitignored since they contain rendered credential values.

**Layer 5: Operational controls**
`config.json` contains `webhook.auto_remediate: true/false`. Setting this to `false` causes the webhook to log `SKIPPED` for all incoming firing alerts without executing any scripts — this serves as a maintenance window flag. Individual remediation scripts also re-check this flag before executing, providing a belt-and-suspenders guard.

---

## Storage and Retention

| Store | Type | Retention | Location | Notes |
|---|---|---|---|---|
| Prometheus TSDB | Time-series metrics | 15 days | Docker volume: `prometheus-data` | Controlled by `--storage.tsdb.retention.time=15d` startup flag |
| Loki chunks | Log storage | 90 days | Docker volume: `loki-data` → `/loki/chunks` | Controlled by `limits_config.retention_period: 2160h` |
| Loki index (legacy) | boltdb-shipper | Until compacted | `/loki/boltdb-shipper-active` + cache | Pre-2026-05-02 data; read-only after schema migration |
| Loki index (current) | tsdb | 90 days | `/loki/tsdb-active` + cache | Active index for all new ingestion from 2026-05-02 |
| Loki WAL | Write-ahead log | Until flushed | `/loki/wal` | Chunk idle flush: 5 min; retain: 30s after flush |
| Alertmanager state | Silences + notifications | Persistent | Docker volume: `alertmanager-data` | Survives container restarts |
| Grafana state | Dashboards + users | Persistent | Docker volume: `grafana-data` | Provisioned dashboards overlaid from `./grafana/dashboards` |
| Remediation log | Text append-only | Manual rotation | `auto-remediation.log` (host filesystem) | Structured key=value format; `os.fsync()` on every write |
| CWV vitals log | JSON lines | Manual rotation | `/var/log/cwv/vitals.log` (remote server) | Written by Playwright cron every 3 min |

---

## Network Topology

```
Internet
    │
    │ HTTPS :443
    ▼
10.10.2.21 (Remote Application Server)
┌──────────────────────────────────────────────┐
│  Nginx :80/:443   ← public-facing            │
│  PHP-FPM          ← unix socket only         │
│  MySQL :3306      ← localhost only           │
│  Redis :6379      ← localhost only           │
│  Node Exporter :9100  ← LAN-accessible       │
│  Nginx Exporter  :9113 ← LAN-accessible      │
│  Promtail :9080   ← LAN-accessible           │
│  CrowdSec         ← host-level IDS           │
└────────────────────┬─────────────────────────┘
                     │
                     │ LAN: 10.10.2.0/24
                     │
10.10.2.77 (Monitoring Laptop)
┌──────────────────────────────────────────────┐
│                                              │
│  HOST-LEVEL SERVICES                         │
│  ─────────────────                           │
│  gunicorn/webhook :5051  0.0.0.0             │
│                                              │
│  DOCKER: aiops-network 172.20.0.0/16         │
│  ┌────────────────────────────────────────┐  │
│  │  Loki         172.20.x.x               │  │
│  │    LAN-exposed: LOCAL_IP:3100          │  │
│  │  Prometheus   172.20.x.x               │  │
│  │    LAN-exposed: 127.0.0.1:9090         │  │
│  │  Alertmanager 172.20.x.x               │  │
│  │    LAN-exposed: 127.0.0.1:9093         │  │
│  │  Grafana      172.20.x.x               │  │
│  │    LAN-exposed: LOCAL_IP:3000          │  │
│  │  Pushgateway  172.20.x.x               │  │
│  │    LAN-exposed: LOCAL_IP:9091          │  │
│  │  Node Exporter 172.20.x.x              │  │
│  │    LAN-exposed: 127.0.0.1:9100         │  │
│  │  cAdvisor     172.20.x.x               │  │
│  │    LAN-exposed: 127.0.0.1:8080         │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘

Port Exposure Summary:
  Public (internet-facing):   NONE — all services are LAN-only
  LAN-accessible (10.10.2.0/24):
    10.10.2.77:3000  Grafana
    10.10.2.77:3100  Loki   (Promtail push target)
    10.10.2.77:9091  Pushgateway (CWV metrics push)
  Localhost-only (127.0.0.1):
    9090  Prometheus
    9093  Alertmanager
    9100  Node Exporter (local)
    8080  cAdvisor
  Host (0.0.0.0 — should be hardened to 127.0.0.1):
    5051  Remediation webhook
```

---

## Known Limitations and Future Work

**Single point of failure — monitoring machine:** The monitoring laptop (10.10.2.77) hosts the entire observability stack. If it goes offline, all alerting and auto-remediation stops silently. A minimal high-availability approach would be to run a secondary Prometheus instance on the remote server itself (scraping local exporters) with Alertmanager federation.

**In-memory deduplication state:** The webhook's `_fired_alerts` dictionary is in-memory only. If the gunicorn service restarts during an active incident, the 30-second dedup window resets and a remediation script could be re-executed unnecessarily. Persisting the dedup state to Redis or a SQLite file would resolve this.

**`generic_triage.sh` is a decision-router stub:** The generic triage script currently queries Loki and Prometheus to build a decision matrix and routes to a specific remediation script. The decision logic should be continuously refined as new failure modes are observed in production.

**Password-based SSH:** The current `ssh_helper.py` supports both SSH key and password authentication, with password as the active fallback. Migrating to key-based authentication exclusively (with `~/.ssh/authorized_keys` on the remote server) would eliminate the risk of the sudo password appearing in process lists.

**No metrics for the remediation engine itself:** The webhook does not expose a `/metrics` endpoint. Adding Prometheus instrumentation (counters for BLOCKED/SUCCESS/FAILED/TIMEOUT outcomes by script name) would allow Prometheus to alert on remediation engine health and track automation effectiveness over time.

**Loki ruler requires `fake` tenant:** Because Loki is running with `auth_enabled: false`, Loki's ruler requires alert rules to be placed in the `fake` tenant directory (`loki/rules/fake/`). This is the correct approach for single-tenant Loki deployments and is not a bug, but the directory name is counterintuitive.
