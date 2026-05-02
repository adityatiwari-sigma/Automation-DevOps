# AIOps Infrastructure Monitoring & Auto-Remediation Platform

> A production-grade, config-driven AIOps platform that monitors client web infrastructure, fires tiered alerts, and automatically heals common failures — reducing mean time to recovery from hours to seconds.

---

## Who Should Read Which Section

| Audience | Relevant Sections |
|---|---|
| **Business / Project Manager** | Executive Summary, Business Value |
| **New Engineer Taking Over** | System Architecture, Key Capabilities, Technology Stack, Prerequisites, Quick Start, Configuration Reference, Deployment Guide |
| **Engineer Operating Day-to-Day** | How Auto-Remediation Works, Grafana Dashboards, Adding a New Monitored Site, Maintenance Schedule |
| **Engineer Debugging an Incident** | Troubleshooting, Support — and then open `RUNBOOK.md` |

---

## Executive Summary

This platform is an AI-assisted operations (AIOps) system built for Sigma Informatics to monitor and automatically recover client web infrastructure. It continuously collects metrics and logs from production servers, evaluates them against a library of alerting rules, and — for known failure patterns — executes targeted remediation scripts over SSH without requiring human intervention.

The system solves a class of problems that manual monitoring cannot: the window between a failure starting and a human noticing is often 15–30 minutes, during which users experience errors and revenue is lost. This platform detects anomalies within seconds of their onset, pages the on-call engineer immediately, and for the most common failure types (PHP-FPM worker exhaustion, Nginx file descriptor limits, Redis memory pressure, HTTP 5xx spikes) executes a fix automatically while the engineer is still reading the alert email.

The business value is measurable: auto-remediated incidents that previously required 20–60 minutes of engineer time are now resolved in under 2 minutes. Core Web Vitals (LCP, CLS, INP, TTFB, FCP) are measured every 3 minutes using real browser automation, giving objective data for SEO impact analysis and UX regression detection. Every alert, every remediation action, and every resolved incident is logged with full evidence — creating a permanent audit trail for post-mortems and compliance reviews.

---

## Business Value

- **24/7 uptime monitoring** — Prometheus scrapes metrics every 15 seconds; Promtail ships logs continuously. Failures surface in seconds, not minutes.
- **Automatic MTTR reduction** — Known failure patterns trigger remediation scripts automatically via SSH. PHP-FPM reloads, Nginx file-limit fixes, and Redis cache flushes complete without human involvement, often before users notice.
- **Proactive alerting** — The three-tier alert system (P1/P2/P3) ensures critical issues escalate immediately while low-priority warnings are batched into daily digests, preventing alert fatigue.
- **Root-cause correlation** — When LCP degrades, the system automatically diagnoses whether the cause is CPU saturation, PHP-FPM worker exhaustion, or slow MySQL queries — and sends an enriched email with the root cause labelled, not just "the site is slow."
- **Core Web Vitals tracking for SEO / UX** — Playwright runs real browser probes every 3 minutes, measuring LCP, CLS, INP, TTFB, and FCP. Regressions are caught before Google's crawlers see them.
- **Full audit trail** — `auto-remediation.log` records every action with timestamp, trigger, diagnosis, duration, and evidence. Alertmanager sends resolved emails with a Root Cause Analysis summary. Nothing is a black box.
- **Inhibition rules** — When a P1 fires, P2 and P3 alerts for the same platform are suppressed, ensuring on-call engineers receive one actionable notification rather than a flood.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     MONITORING LAPTOP  10.10.2.77                               │
│                                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐   ┌───────────────┐  │
│  │  Prometheus  │   │     Loki     │   │  Alertmanager  │   │    Grafana    │  │
│  │   :9090      │◄──│   :3100      │   │    :9093       │   │    :3000      │  │
│  │              │   │              │   │                │   │               │  │
│  │ Scrapes:     │   │ Receives     │   │ Routes alerts  │   │ 5 Dashboards  │  │
│  │ - node-exp   │   │ logs from    │   │ by severity    │   │ CWV, Errors,  │  │
│  │ - nginx-exp  │   │ Promtail     │   │ P1/P2/P3       │   │ LCP Correl.,  │  │
│  │ - promtail   │   │              │   │                │   │ Playwright,   │  │
│  │ - pushgw     │   │ Evaluates    │   │ Sends email    │   │ Project       │  │
│  │ - cadvisor   │   │ Loki alert   │   │ notifications  │   │               │  │
│  │              │   │ rules        │   │                │   │ Datasources:  │  │
│  │ Evaluates    │   │              │   │ Calls webhook  │   │ Prometheus    │  │
│  │ metric alert │   └──────────────┘   │ for remediated │   │ + Loki        │  │
│  │ rules        │         ▲            │ alerts         │   └───────────────┘  │
│  └──────┬───────┘         │            └──────┬─────────┘                      │
│         │ fires alerts    │ log push           │ POST /webhook                  │
│         └─────────────────┘ (Promtail→Loki)   │ Bearer token                  │
│                                                ▼                                │
│  ┌────────────────┐   ┌──────────────────────────────────────────────────────┐  │
│  │  Pushgateway   │   │         Webhook Service  :5051  (Flask/Gunicorn)     │  │
│  │   :9091        │   │                                                      │  │
│  │                │   │  Receives alert JSON → spawns background thread     │  │
│  │ Receives CWV   │   │  Deduplicates (30s window)                          │  │
│  │ metrics from   │   │  Validates script against allowlist                 │  │
│  │ Playwright     │   │  Runs bash script → ssh_helper.py → SSH → server   │  │
│  │ probe          │   │  Logs every action to auto-remediation.log          │  │
│  └────────────────┘   │  Sends resolved email with RCA summary              │  │
│         ▲             └──────────────────────┬───────────────────────────────┘  │
│         │ push metrics                        │ SSH (Paramiko)                  │
└─────────┼────────────────────────────────────┼────────────────────────────────-┘
          │                                     │
          │ HTTP push (every 3 min)             │ Execute scripts via sudo
          │                                     │
┌─────────┼─────────────────────────────────────┼────────────────────────────────┐
│         │       REMOTE SERVER  10.10.2.21      │                                │
│         │                                     ▼                                │
│  ┌──────┴─────────────────────────────────────────────────┐                    │
│  │                  Playwright Cron (every 3 min)         │                    │
│  │  Measures: LCP, CLS, INP, TTFB, FCP                   │                    │
│  │  Target: dev.regenics.com                              │                    │
│  └─────────────────────────────────────────────────────────┘                   │
│                                                                                 │
│  ┌───────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────────────┐   │
│  │  Nginx        │  │  PHP-FPM    │  │  MySQL      │  │  Redis  :6379     │   │
│  │  (web server) │  │  (PHP apps) │  │  (database) │  │  DB0=cache        │   │
│  │               │  │             │  │             │  │  DB1=sessions     │   │
│  │  Laravel /    │  │  Pool: www  │  │             │  │                   │   │
│  │  WordPress /  │  │             │  │             │  └───────────────────┘   │
│  │  Magento      │  └─────────────┘  └─────────────┘                          │
│  └───────────────┘                                                              │
│                                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────────────┐       │
│  │  Node Exporter   │  │  Nginx Exporter  │  │  Promtail  :9080        │       │
│  │  :9100           │  │  :9113           │  │                         │       │
│  │  CPU/Mem/Disk    │  │  HTTP req counts │  │  Ships nginx, php-fpm,  │       │
│  │  metrics         │  │  status codes    │  │  mysql, redis logs →    │       │
│  └──────────────────┘  └──────────────────┘  │  Loki :3100             │       │
│                                              │  Exposes latency        │       │
│  ┌──────────────────────────────────┐        │  histogram metrics      │       │
│  │  CrowdSec                        │        └─────────────────────────┘       │
│  │  Security decisions & banning    │                                           │
│  └──────────────────────────────────┘                                           │
└─────────────────────────────────────────────────────────────────────────────────┘

DATA FLOWS:
  Metrics (pull):   Prometheus ← Node Exporter, Nginx Exporter, Promtail, cAdvisor
  Metrics (push):   Playwright → Pushgateway ← Prometheus
  Logs (push):      Promtail → Loki
  Alert flow:       Prometheus/Loki rules → Alertmanager → email + webhook
  Remediation:      Webhook → bash script → ssh_helper.py → SSH → remote server
  Verification:     Webhook → ssh_helper.py → SSH → systemctl status / redis-cli
```

---

## Key Capabilities

1. **Dual-signal alerting** — Both Prometheus metric rules and Loki log-pattern rules feed into Alertmanager. This means a PHP-FPM crash detected in logs and an HTTP 5xx spike detected in Nginx metrics both trigger alerts through the same routing tree, with deduplication and inhibition applied uniformly.

2. **Three-tier severity routing** — Alerts are classified P1 (Critical, 15-minute repeat interval), P2 (High, 1-hour repeat interval), or P3 (Low, 24-hour daily digest). Alertmanager routes each tier to the appropriate receiver and applies inhibition rules to suppress lower-severity noise when a critical incident is already active.

3. **Automated root-cause correlation** — When LCP exceeds 4000ms, correlation rules fire and diagnose the root cause: CPU bottleneck (>85% CPU via Node Exporter), PHP-FPM worker exhaustion (max_children events in logs via Loki), or slow MySQL queries (Query_time events in logs via Loki). If infra metrics are normal, the rule points to a recent code deployment. Each correlation fires a separate enriched email with the root_cause label set.

4. **Auto-remediation with safety guards** — The webhook service dispatches remediation scripts over SSH. The `redis-flush-cache.sh` script has three production safety guards: it checks `auto_remediate` in config.json, refuses to run if cache_db equals session_db (preventing session data loss), and only flushes if memory usage is above 90%. The `generic_triage.sh` script queries both Prometheus and Loki before deciding which specific script to run.

5. **Config-driven architecture** — A single `config.json` is the sole source of truth. Running `python3 generate_configs.py` regenerates `.env`, `prometheus.yml`, `alertmanager.yml`, and `promtail-config.yml` from templates. No YAML file is edited directly in production — changes are made to `config.json` or a template, then regenerated.

6. **Real-browser Core Web Vitals probes** — Playwright runs every 3 minutes against the target site, measuring LCP, CLS, INP, TTFB, and FCP with a real Chromium browser. Results are pushed to Pushgateway and pulled by Prometheus. Grafana dashboards visualize trends over time, and Prometheus alert rules fire when LCP exceeds the 4000ms good/needs-improvement threshold.

---

## Technology Stack

| Component | Version | Role | Port |
|---|---|---|---|
| Prometheus | 2.51.0 | Metrics collection, alert rule evaluation | 9090 |
| Loki | 2.9.0 | Log aggregation, Loki alert rule evaluation | 3100 |
| Alertmanager | 0.27.0 | Alert routing, deduplication, email dispatch | 9093 |
| Grafana | 10.4.0 | Dashboards, visualization | 3000 |
| Pushgateway | 1.8.0 | Receives Playwright CWV push metrics | 9091 |
| Node Exporter | 1.7.0 | Host CPU / memory / disk metrics (both machines) | 9100 |
| Nginx Exporter | latest | Nginx HTTP request counts and status codes | 9113 |
| Promtail | 2.9.0 | Log shipping from remote server to Loki | 9080 |
| cAdvisor | 0.49.1 | Per-container resource usage on monitoring laptop | 8080 |
| Flask / Gunicorn | Python 3 | Webhook service for auto-remediation dispatch | 5051 |
| Paramiko | latest | SSH client library used by ssh_helper.py | — |
| Playwright | latest | Real-browser Core Web Vitals measurement | — |
| CrowdSec | latest | Security decisions and IP banning on remote server | — |

All monitoring-stack components run in Docker containers on the monitoring laptop (10.10.2.77) via Docker Compose. Node Exporter, Nginx Exporter, and Promtail run natively on the remote server (10.10.2.21).

---

## Prerequisites

### Monitoring Laptop (10.10.2.77)

- Docker Engine 24+ and Docker Compose v2 (`docker compose` subcommand)
- Python 3.9+ with `pip`
- Network access to remote server on ports 9100, 9113, 9080
- Outbound SMTP access (port 587 or 465) for email alerts
- Sufficient disk space: at minimum 20 GB for Prometheus TSDB (15-day retention) and Loki chunk storage

```bash
# Verify Docker
docker --version && docker compose version

# Verify Python
python3 --version

# Install Python dependencies (Flask, Paramiko, Gunicorn)
pip3 install -r requirements.txt
```

### Remote Server (10.10.2.21)

- Ubuntu 20.04+ or Debian 11+ (systemd required)
- Nginx, PHP-FPM (8.4), MySQL, Redis already installed and running
- `node_exporter` service running on port 9100
- `nginx-prometheus-exporter` running on port 9113
- Promtail binary installed (see Deployment Guide)
- SSH access from 10.10.2.77 using the credentials in `config.json`
- The SSH user must have passwordless sudo for `systemctl`, `sysctl`, and `redis-cli` (or provide sudo password in config)

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url> Automation-DevOps
cd Automation-DevOps

# 2. Create your config file
cp config.example.json config.json
# Edit config.json — fill in all CHANGE_ME fields:
#   - grafana.admin_password
#   - email.app_password and recipients
#   - ssh.user, ssh.password, ssh.sudo_password
#   - webhook.secret_token  (generate with: openssl rand -hex 32)
#   - platform.name and instance_name
nano config.json

# 3. Validate config and generate all YAML files
python3 generate_configs.py --check   # validate only
python3 generate_configs.py           # generate .env + all YAMLs

# 4. Start the Docker stack
docker compose up -d

# 5. Verify health
docker compose ps
curl -s http://localhost:9090/-/healthy   # Prometheus
curl -s http://localhost:9093/-/healthy   # Alertmanager
curl -s http://localhost:3100/ready       # Loki
curl -s http://localhost:5051/health      # Webhook service
```

Open Grafana at `http://10.10.2.77:3000` and log in with `admin` / your configured password.

---

## Configuration Reference

All platform configuration lives in `config.json`. After any change, run `python3 generate_configs.py` and reload the affected services.

| Key Path | Type | Description | Example |
|---|---|---|---|
| `network.local_ip` | string | IP of the monitoring laptop running Docker | `"10.10.2.77"` |
| `network.remote_ip` | string | IP of the remote server hosting client sites | `"10.10.2.21"` |
| `ports.grafana` | integer | Grafana container port | `3000` |
| `ports.prometheus` | integer | Prometheus container port | `9090` |
| `ports.loki` | integer | Loki container port | `3100` |
| `ports.alertmanager` | integer | Alertmanager container port | `9093` |
| `ports.pushgateway` | integer | Pushgateway container port | `9091` |
| `ports.webhook` | integer | Flask webhook service port | `5051` |
| `ports.node_exporter` | integer | Node Exporter port (remote server) | `9100` |
| `ports.nginx_exporter` | integer | Nginx Exporter port (remote server) | `9113` |
| `ports.promtail` | integer | Promtail port (remote server) | `9080` |
| `ports.cadvisor` | integer | cAdvisor port (monitoring laptop) | `8080` |
| `grafana.admin_user` | string | Grafana admin username | `"admin"` |
| `grafana.admin_password` | string | Grafana admin password — never use default | `"StrongPassword123"` |
| `email.smtp_host` | string | SMTP server and port | `"smtp.gmail.com:587"` |
| `email.from_address` | string | Sender email address | `"alerts@example.com"` |
| `email.app_password` | string | Gmail App Password (not your login password) | `"abcd efgh ijkl mnop"` |
| `email.recipients.p1` | string | Email recipient for P1 Critical alerts | `"oncall@example.com"` |
| `email.recipients.p2` | string | Email recipient for P2 High alerts | `"team@example.com"` |
| `email.recipients.p3` | string | Email recipient for P3 Low digest alerts | `"team@example.com"` |
| `email.recipients.correlation` | string | Email recipient for LCP correlation reports | `"oncall@example.com"` |
| `ssh.user` | string | SSH username on the remote server | `"ubuntu"` |
| `ssh.password` | string | SSH password for the remote server user | `"ssh-password"` |
| `ssh.sudo_password` | string | Sudo password for privileged commands | `"sudo-password"` |
| `redis.port` | integer | Redis port on the remote server | `6379` |
| `redis.cache_db` | integer | Redis database index for application cache | `0` |
| `redis.session_db` | integer | Redis database index for sessions (must differ from cache_db) | `1` |
| `redis.password` | string | Redis AUTH password (leave empty string if none) | `""` |
| `slack.webhook_url` | string | Optional Slack incoming webhook for Redis flush notifications | `""` |
| `webhook.secret_token` | string | Bearer token for Alertmanager→Webhook auth. Generate with `openssl rand -hex 32` | `"abc123..."` |
| `webhook.auto_remediate` | boolean | Master switch for auto-remediation. Set `false` during maintenance | `true` |
| `platform.name` | string | Human-readable platform name used in alert labels | `"dev.regenics.com"` |
| `platform.instance_name` | string | Short machine-readable identifier for Prometheus labels | `"dev-regenics"` |
| `platform.environment` | string | Environment label attached to all alerts | `"production"` |

---

## Deployment Guide

### First-Time Deployment: Docker Stack (Monitoring Laptop)

```bash
# Step 1: Ensure Docker is installed and running
sudo systemctl status docker

# Step 2: Complete Quick Start steps 1–3 (clone, config.json, generate_configs.py)

# Step 3: Start the full stack
cd /path/to/Automation-DevOps
docker compose up -d

# Step 4: Watch startup logs — Loki must be healthy before Prometheus starts
docker compose logs -f loki prometheus alertmanager

# Step 5: Verify all containers are running
docker compose ps
# Expected: node-exporter, cadvisor, loki, prometheus, alertmanager, grafana, pushgateway

# Step 6: Start the webhook service (systemd)
sudo cp auto-remediation-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable auto-remediation-webhook
sudo systemctl start auto-remediation-webhook
sudo systemctl status auto-remediation-webhook

# Step 7: Check webhook health
curl http://localhost:5051/health
# Expected: {"status": "ok", "auto_remediate": true}

# Step 8: Import Grafana dashboards
# Dashboards are auto-provisioned from grafana/dashboards/*.json
# Open http://10.10.2.77:3000 → Dashboards → verify 5 dashboards are present
```

### Deploying Exporters on the Remote Server (10.10.2.21)

```bash
# --- Node Exporter ---
# Download and install
wget https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz
tar xf node_exporter-1.7.0.linux-amd64.tar.gz
sudo mv node_exporter-1.7.0.linux-amd64/node_exporter /usr/local/bin/

# Create systemd service
sudo tee /etc/systemd/system/node_exporter.service > /dev/null <<'EOF'
[Unit]
Description=Node Exporter
After=network.target

[Service]
User=node_exporter
ExecStart=/usr/local/bin/node_exporter
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo useradd -rs /bin/false node_exporter 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable node_exporter
sudo systemctl start node_exporter
# Verify: curl http://localhost:9100/metrics | head -5

# --- Nginx Prometheus Exporter ---
# Requires nginx stub_status enabled at /nginx_status
# In nginx site config: location /nginx_status { stub_status on; allow 127.0.0.1; deny all; }
wget https://github.com/nginxinc/nginx-prometheus-exporter/releases/download/v1.1.0/nginx-prometheus-exporter_1.1.0_linux_amd64.tar.gz
tar xf nginx-prometheus-exporter_1.1.0_linux_amd64.tar.gz
sudo mv nginx-prometheus-exporter /usr/local/bin/
# Run as: nginx-prometheus-exporter -nginx.scrape-uri http://localhost/nginx_status

# --- Promtail ---
# Copy the generated promtail/promtail-config.yml to the remote server
scp promtail/promtail-config.yml user@10.10.2.21:/etc/promtail/config.yml

# Download Promtail binary (match Loki version: 2.9.0)
wget https://github.com/grafana/loki/releases/download/v2.9.0/promtail-linux-amd64.zip
unzip promtail-linux-amd64.zip
sudo mv promtail-linux-amd64 /usr/local/bin/promtail

sudo tee /etc/systemd/system/promtail.service > /dev/null <<'EOF'
[Unit]
Description=Promtail log shipper
After=network.target

[Service]
User=root
ExecStart=/usr/local/bin/promtail -config.file=/etc/promtail/config.yml
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable promtail
sudo systemctl start promtail
# Verify: curl http://localhost:9080/metrics | head -5

# --- Playwright Cron (Core Web Vitals probe) ---
# See server-config/ directory for the Playwright cron job script
# The cron job runs every 3 minutes and pushes metrics to Pushgateway
crontab -l  # verify: */3 * * * * /path/to/playwright-probe.sh
```

---

## How Auto-Remediation Works

The auto-remediation loop involves six components: an alerting rule, Alertmanager, the webhook service, a remediation script, SSH, and a verification step.

```
1. ALERT FIRES
   A Prometheus metric rule or Loki log rule evaluates to true.
   The alert includes a 'remediation' label (e.g., remediation: fpm-reload).

2. ALERTMANAGER ROUTING
   Alertmanager matches the 'remediation' label regex.
   It calls the auto-remediation-webhook receiver FIRST (continue: true),
   then routes the alert to the appropriate P1/P2 email receiver.

3. WEBHOOK RECEIVES ALERT
   POST /webhook arrives at Flask service on port 5051.
   Bearer token is validated against config.json webhook.secret_token.
   Request body is capped at 1 MB.
   A background thread is spawned per alert.

4. DEDUPLICATION
   If the same fingerprint fired within the last 30 seconds, it is silently dropped.
   This prevents thundering-herd re-fires from triggering multiple SSH sessions.

5. SCRIPT DISPATCH
   The 'remediation' label value is validated against the ALLOWED_SCRIPTS frozenset:
     {fpm-reload, nginx-file-limit, redis-flush-cache, generic_triage}
   Path traversal is blocked via os.path.realpath() checks.
   The script receives alert labels as LABEL_* environment variables.

6. SSH EXECUTION
   The script calls remediate/ssh_helper.py which uses Paramiko to SSH into
   the remote server (10.10.2.21) and execute the command with sudo.
   Timeout: 90 seconds per script execution.

7. LOGGING
   Every execution is appended to auto-remediation.log with:
   TRIGGER, PLATFORM, DIAGNOSIS, ACTION, STATUS, DURATION, EVIDENCE

8. VERIFICATION
   When Alertmanager sends a 'resolved' event, the webhook runs a post-remediation
   verification command over SSH (e.g., systemctl status php8.4-fpm).

9. RESOLVED EMAIL
   The webhook sends a resolved email containing:
   - The original trigger and action taken
   - Execution duration
   - Post-remediation verification output
   - Full Root Cause Analysis summary
```

### Remediation Scripts

| Script | Trigger Label | What It Does | Safety Guards |
|---|---|---|---|
| `fpm-reload.sh` | `fpm-reload` | SSH → `systemctl reload php8.4-fpm` | Checks `auto_remediate` flag |
| `nginx-file-limit.sh` | `nginx-file-limit` | SSH → `sysctl -w fs.file-max=100000` + `systemctl reload nginx` | Checks `auto_remediate` flag |
| `redis-flush-cache.sh` | `redis-flush-cache` | Flushes Redis cache DB only (not session DB) via `redis-cli FLUSHDB` | 3 guards: auto_remediate flag, cache_db ≠ session_db, memory > 90% threshold |
| `generic_triage.sh` | `generic_triage` | Queries Prometheus + Loki for evidence, then dispatches to the appropriate specific script | Queries both signal sources before acting |

---

## Grafana Dashboards

| Dashboard Name | Purpose | Key Panels | URL Path |
|---|---|---|---|
| Core Web Vitals | Track LCP, CLS, INP, TTFB, FCP from Playwright probes over time | LCP trend, CLS gauge, INP p75, TTFB heatmap, FCP timeline | `/d/cwv-dashboard` |
| Error Groups | Visualize HTTP error rates grouped by status code, URL, and platform | 5xx rate over time, top error URLs, error group table, PHP fatal log stream | `/d/error-groups-v2` |
| LCP Correlation | Root-cause dashboard: LCP vs CPU, FPM worker saturation, MySQL slow queries | LCP vs CPU overlay, FPM active/max gauge, slow query log stream, correlation alert history | `/d/lcp-correlation` |
| Playwright Probing | Detailed per-probe breakdown of individual CWV measurements | Per-URL probe results, geographic breakdowns, probe success rate | `/d/playwright-probing` |
| Project Dashboard | High-level overview for stakeholders: all services, alert summary, uptime | Service status grid, active alert count, uptime percentage, recent incidents | `/d/project-dashboard` |

All dashboards are auto-provisioned from `grafana/dashboards/*.json` at container startup. To update a dashboard, export the JSON from Grafana UI, overwrite the corresponding file, and restart the Grafana container.

---

## Adding a New Monitored Site

When onboarding a new client site, follow these steps in order.

```bash
# 1. Add Prometheus scrape targets
# Edit: prometheus/prometheus.yml.template
# Add under scrape_configs:
#
#   - job_name: "nginx-exporter-newsite"
#     static_configs:
#       - targets: ["<NEW_SERVER_IP>:9113"]
#         labels:
#           service: nginx
#           platform: "new.client.com"
#           environment: "production"
#           instance_name: "new-client"
#
#   - job_name: "node-exporter-newsite"
#     static_configs:
#       - targets: ["<NEW_SERVER_IP>:9100"]
#         labels:
#           platform: "new.client.com"
#           instance_name: "new-client"

# 2. Add Promtail scrape job for the new server's logs
# Edit: promtail/promtail-config.yml.template
# Add a new scrape_config pointing at the new server's log paths

# 3. Add alert rules for the new platform
# Edit: prometheus/rules/app-alerts.yaml (or create a new file)
# Add platform label: platform: "new.client.com" to new rules
# For Loki rules: edit loki/rules/fake/loki-alerts.yaml

# 4. Regenerate configs
python3 generate_configs.py

# 5. Reload Prometheus (no restart needed for rule changes)
curl -X POST http://localhost:9090/-/reload

# 6. Reload Alertmanager if alertmanager.yml changed
curl -X POST http://localhost:9093/-/reload

# 7. Reload Loki rules
curl -X POST http://localhost:3100/loki/api/v1/rules/reload 2>/dev/null || \
  docker restart loki

# 8. Deploy exporters on the new server
# Follow the same steps as in the Deployment Guide for the remote server

# 9. Verify new targets appear in Prometheus
# Open http://10.10.2.77:9090/targets — new jobs should show green (UP)
```

---

## Maintenance Schedule

| Task | Frequency | Command |
|---|---|---|
| Check all scrape targets are UP | Daily | `curl -s http://localhost:9090/api/v1/targets \| python3 -m json.tool \| grep '"health"'` |
| Review auto-remediation log | Daily | `tail -100 /path/to/Automation-DevOps/auto-remediation.log` |
| Check Prometheus disk usage | Weekly | `docker system df -v \| grep prometheus` |
| Check Loki disk usage | Weekly | `docker system df -v \| grep loki` |
| Rotate remediation log | Weekly | `truncate -s 0 auto-remediation.log` (after backing up) |
| Verify email delivery | Weekly | `python3 test_email.py` |
| Test alert pipeline end-to-end | Monthly | `bash trigger_alerts.sh` (fires test alerts) |
| Update container images | Monthly | `docker compose pull && docker compose up -d` |
| Review and rotate webhook secret token | Quarterly | `openssl rand -hex 32` → update config.json → regenerate → reload |
| Review Prometheus retention policy | Quarterly | Check `--storage.tsdb.retention.time` in docker-compose.yml |
| Backup Grafana dashboards | Monthly | Export dashboard JSON from UI → commit to git |
| Verify SSH key/password still valid | Monthly | `python3 -c "from remediate.ssh_helper import *; print('ok')"` |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| No alerts arriving by email | SMTP credentials wrong or Gmail App Password expired | Check `email.app_password` in config.json. Run `python3 test_email.py`. Check Alertmanager logs: `docker logs alertmanager` |
| Webhook returns 401 | Alertmanager bearer token doesn't match webhook secret | Verify `webhook.secret_token` in config.json matches `credentials` in alertmanager.yml. Run `python3 generate_configs.py`. |
| Prometheus shows target DOWN | Exporter not running on remote server, or firewall blocking port | SSH to remote server and check: `systemctl status node_exporter`. Test from laptop: `curl http://10.10.2.21:9100/metrics \| head` |
| Loki not receiving logs | Promtail stopped or config error | On remote server: `systemctl status promtail`. Check Promtail logs: `journalctl -u promtail -n 50` |
| Auto-remediation not running | `auto_remediate: false` in config.json, or webhook service down | Check: `curl http://localhost:5051/health`. Check systemd: `systemctl status auto-remediation-webhook` |
| Redis flush script aborted (safety guard) | `redis.cache_db` equals `redis.session_db` in config.json | Set `redis.cache_db` to `0` and `redis.session_db` to `1` (different values). Re-run `generate_configs.py` |
| Grafana dashboards blank / no data | Prometheus or Loki datasource not connected | In Grafana → Configuration → Data Sources → Test each datasource. Verify containers are running |
| Docker compose up fails with "password not set" | `.env` file not generated | Run `python3 generate_configs.py` first |
| Playwright CWV metrics not appearing | Playwright cron stopped on remote server, or Pushgateway unreachable | Check crontab on remote server: `crontab -l`. Check Pushgateway: `curl http://10.10.2.77:9091/metrics` |
| generate_configs.py fails with "CONFIG ERRORS" | config.json still has `CHANGE_ME` placeholders | Fill in all placeholder values in config.json before running |
| Loki alert rules not firing | Loki schema or rule directory misconfigured | Check: `docker logs loki \| grep -i error`. Verify `loki/rules/fake/` directory exists with YAML files |
| SSH remediation times out | Remote server overloaded or SSH service down | Check from laptop: `ssh user@10.10.2.21`. Check ssh_helper.py timeout (90s default) |
| `P1 5xx` alert fires but FPM reload fails | PHP-FPM service name mismatch | The script uses `php8.4-fpm`. If your server runs a different version, update `fpm-reload.sh` |

---

## Support

**Platform Owner:** Aditya Tiwari, DevOps Engineer, Sigma Informatics

**Logs to check first:**
- Auto-remediation actions: `Automation-DevOps/auto-remediation.log`
- Webhook service: `Automation-DevOps/remediation-webhook.log` or `journalctl -u auto-remediation-webhook -n 100`
- Prometheus: `docker logs prometheus`
- Loki: `docker logs loki`
- Alertmanager: `docker logs alertmanager`
- Grafana: `docker logs grafana`

**Key URLs:**
- Grafana: `http://10.10.2.77:3000`
- Prometheus: `http://10.10.2.77:9090`
- Alertmanager: `http://10.10.2.77:9093`
- Webhook status page: `http://10.10.2.77:5051`

**For incidents in progress, open `RUNBOOK.md`** — it contains step-by-step procedures for every alert type, with exact commands.
