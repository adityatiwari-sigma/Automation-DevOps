# Server-Side Configuration — Remote Server (`remote_ip` from config.json)

These are optional server-side config changes on the remote web server to enable
richer correlation data. **No new exporters are required** — all signals come from
logs already being pushed by Promtail to Loki, plus the existing Node Exporter
and Nginx Exporter.

---

## 1. MySQL Slow Query Log (Recommended)

Enabling the slow query log gives the correlation dashboard detailed slow query
entries for the drill-down panel. The existing Promtail MySQL job
(`__path__: /var/log/mysql/*.log`) will automatically pick up this log file.

Edit `/etc/mysql/mysql.conf.d/mysqld.cnf` and add under `[mysqld]`:

```ini
[mysqld]
slow_query_log         = 1
slow_query_log_file    = /var/log/mysql/mysql-slow.log
long_query_time        = 1
log_queries_not_using_indexes = 1
```

Then restart MySQL:

```bash
sudo systemctl restart mysql
```

Verify:

```bash
mysql -u root -e "SHOW VARIABLES LIKE 'slow_query_log%';"
mysql -u root -e "SHOW VARIABLES LIKE 'long_query_time';"
```

> **Note:** The existing Promtail config already scrapes `/var/log/mysql/*.log`
> with `platform: mysql` label. The slow query log entries will appear in Loki
> and be queryable in the correlation dashboard's drill-down panel.

---

## 2. PHP-FPM Logging (Already Configured)

The existing Promtail config scrapes `/var/log/php*-fpm.log` with
`platform: php-fpm` label. When PHP-FPM hits `pm.max_children`, it logs a
warning line that the Loki correlation rule detects automatically.

To verify your PHP-FPM is logging properly:

```bash
# Check that PHP-FPM error log exists and is being written to
ls -la /var/log/php*-fpm.log

# Verify pm.max_children setting (lower values trigger exhaustion sooner)
grep "pm.max_children" /etc/php/*/fpm/pool.d/www.conf
```

---

## 3. What's Already Working (No Changes Needed)

| Signal | Source | Prometheus Job |
|--------|--------|---------------|
| CPU / Memory | Node Exporter on `<remote_ip>`:9100 | `node-exporter-remote` |
| Nginx Connections | Nginx Exporter on `<remote_ip>`:9113 | `nginx-exporter-remote` |
| PHP-FPM Errors | Promtail → Loki (`platform=php-fpm`) | Loki query |
| MySQL Errors | Promtail → Loki (`platform=mysql`) | Loki query |
| LCP / TTFB | Playwright → Pushgateway | `pushgateway` |
