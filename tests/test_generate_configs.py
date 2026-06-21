"""
test_generate_configs.py — unit tests for generate_configs.py

Covers:
  - Template placeholder substitution
  - Validation catches known-bad config values
  - Validation passes on valid config
  - Generated .env file content
  - Missing template file handling (graceful skip)
  - Redis safety check (cache_db != session_db)
"""

import os
import sys
import json
import pytest
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import generate_configs as gc


# ════════════════════════════════════════════════════════════
# TEMPLATE SUBSTITUTION
# ════════════════════════════════════════════════════════════

class TestSubstitution:

    def test_basic_substitution(self):
        """substitute() must replace __KEY__ placeholders."""
        result = gc.substitute("Hello __NAME__, port __PORT__", {"NAME": "world", "PORT": "9090"})
        assert result == "Hello world, port 9090"

    def test_multiple_occurrences_replaced(self):
        """All instances of the same placeholder must be replaced."""
        result = gc.substitute("__X__ and __X__", {"X": "yes"})
        assert result == "yes and yes"

    def test_unreplaced_placeholder_does_not_raise(self):
        """An unreplaced placeholder should be warned about but not raise an exception."""
        result = gc.substitute("__MISSING__", {})
        assert "__MISSING__" in result     # placeholder left in output

    def test_integer_values_converted_to_string(self):
        """Integer replacement values must be converted to string in output."""
        result = gc.substitute("port=__PORT__", {"PORT": 9090})
        assert "port=9090" in result

    def test_no_substitution_when_no_placeholders(self):
        """Text with no placeholders must be returned unchanged."""
        text = "no placeholders here"
        result = gc.substitute(text, {"KEY": "value"})
        assert result == text

    def test_ip_substituted_in_prometheus_template(self):
        """REMOTE_IP must appear in the prometheus template output."""
        template = "targets: [\"__REMOTE_IP__:__NGINX_EXPORTER_PORT__\"]"
        result = gc.substitute(template, {"REMOTE_IP": "10.10.2.21", "NGINX_EXPORTER_PORT": "9113"})
        assert "10.10.2.21:9113" in result

    def test_smtp_credentials_substituted(self):
        """SMTP credentials must be correctly substituted in alertmanager template."""
        template = "smtp_auth_password: \"__SMTP_PASSWORD__\""
        result = gc.substitute(template, {"SMTP_PASSWORD": "myapppassword"})
        assert "myapppassword" in result
        assert "__SMTP_PASSWORD__" not in result


# ════════════════════════════════════════════════════════════
# CONFIG VALIDATION
# ════════════════════════════════════════════════════════════

class TestValidation:

    def _make_cfg(self, overrides: dict = None) -> dict:
        """Build a valid test config, applying optional overrides."""
        cfg = {
            "network":  {"local_ip": "10.10.2.77", "remote_ip": "10.10.2.21"},
            "ports":    {"grafana": 3000, "prometheus": 9090, "loki": 3100,
                         "alertmanager": 9093, "pushgateway": 9091, "webhook": 5051,
                         "node_exporter": 9100, "nginx_exporter": 9113, "promtail": 9080, "cadvisor": 8080},
            "grafana":  {"admin_user": "admin", "admin_password": "StrongP@ss1"},
            "email":    {"smtp_host": "smtp.gmail.com:587", "from_address": "a@b.com",
                         "app_password": "realapppassword123",
                         "recipients": {"p1": "p1@b.com", "p2": "p2@b.com",
                                        "p3": "p3@b.com", "correlation": "c@b.com"}},
            "ssh":      {"user": "ubuntu", "password": "sshpass", "sudo_password": "sudopass"},
            "redis":    {"port": 6379, "cache_db": 0, "session_db": 1, "password": ""},
            "slack":    {"webhook_url": ""},
            "webhook":  {"secret_token": "abc123def456abc123", "auto_remediate": True},
            "platform": {"name": "dev.example.com", "instance_name": "dev", "environment": "production"},
        }
        if overrides:
            for path, value in overrides.items():
                parts = path.split(".")
                node = cfg
                for p in parts[:-1]:
                    node = node[p]
                node[parts[-1]] = value
        return cfg

    def test_valid_config_passes_validation(self):
        """A correctly filled config must pass without raising SystemExit."""
        cfg = self._make_cfg()
        try:
            gc.validate(cfg)
        except SystemExit:
            pytest.fail("validate() raised SystemExit on a valid config")

    def test_grafana_change_me_password_fails(self):
        """CHANGE_ME in grafana.admin_password must cause SystemExit."""
        cfg = self._make_cfg({"grafana.admin_password": "CHANGE_ME"})
        with pytest.raises(SystemExit):
            gc.validate(cfg)

    def test_email_change_me_password_fails(self):
        """CHANGE_ME in email.app_password must cause SystemExit."""
        cfg = self._make_cfg({"email.app_password": "CHANGE_ME_password"})
        with pytest.raises(SystemExit):
            gc.validate(cfg)

    def test_same_redis_dbs_fails(self):
        """cache_db == session_db is a data-loss risk and must fail validation."""
        cfg = self._make_cfg({"redis.cache_db": 0, "redis.session_db": 0})
        with pytest.raises(SystemExit):
            gc.validate(cfg)

    def test_different_redis_dbs_passes(self):
        """Different cache_db and session_db must pass validation."""
        cfg = self._make_cfg({"redis.cache_db": 0, "redis.session_db": 1})
        try:
            gc.validate(cfg)
        except SystemExit:
            pytest.fail("Different Redis DBs should pass validation")


# ════════════════════════════════════════════════════════════
# ENV FILE GENERATION
# ════════════════════════════════════════════════════════════

class TestEnvGeneration:

    def test_env_file_written(self, tmp_path, monkeypatch):
        """generate_env() must create a .env file in the project root."""
        monkeypatch.setattr(gc, "ROOT", str(tmp_path))
        cfg = {
            "network":  {"local_ip": "10.10.2.77", "remote_ip": "10.10.2.21"},
            "ports":    {"grafana": 3000, "prometheus": 9090, "loki": 3100,
                         "alertmanager": 9093, "pushgateway": 9091, "webhook": 5051,
                         "node_exporter": 9100, "nginx_exporter": 9113, "cadvisor": 8080},
            "grafana":  {"admin_user": "admin", "admin_password": "pass"},
            "platform": {"name": "dev.example.com", "environment": "production"},
        }
        gc.generate_env(cfg)
        env_path = tmp_path / ".env"
        assert env_path.exists()

    def test_env_file_contains_local_ip(self, tmp_path, monkeypatch):
        """Generated .env must include LOCAL_IP."""
        monkeypatch.setattr(gc, "ROOT", str(tmp_path))
        cfg = {
            "network":  {"local_ip": "10.10.2.77", "remote_ip": "10.10.2.21"},
            "ports":    {"grafana": 3000, "prometheus": 9090, "loki": 3100,
                         "alertmanager": 9093, "pushgateway": 9091, "webhook": 5051,
                         "node_exporter": 9100, "nginx_exporter": 9113, "cadvisor": 8080},
            "grafana":  {"admin_user": "admin", "admin_password": "pass"},
            "platform": {"name": "dev.example.com", "environment": "production"},
        }
        gc.generate_env(cfg)
        content = (tmp_path / ".env").read_text()
        assert "LOCAL_IP=10.10.2.77" in content
        assert "GRAFANA_ADMIN_PASSWORD=pass" in content

    def test_env_file_not_empty(self, tmp_path, monkeypatch):
        """Generated .env must not be empty."""
        monkeypatch.setattr(gc, "ROOT", str(tmp_path))
        cfg = {
            "network":  {"local_ip": "10.10.2.77", "remote_ip": "10.10.2.21"},
            "ports":    {"grafana": 3000, "prometheus": 9090, "loki": 3100,
                         "alertmanager": 9093, "pushgateway": 9091, "webhook": 5051,
                         "node_exporter": 9100, "nginx_exporter": 9113, "cadvisor": 8080},
            "grafana":  {"admin_user": "admin", "admin_password": "pass"},
            "platform": {"name": "dev.example.com", "environment": "production"},
        }
        gc.generate_env(cfg)
        content = (tmp_path / ".env").read_text()
        assert len(content.strip()) > 0


# ════════════════════════════════════════════════════════════
# TEMPLATE FILE HANDLING
# ════════════════════════════════════════════════════════════

class TestTemplateHandling:

    def test_missing_template_is_skipped_gracefully(self, tmp_path, monkeypatch, capsys):
        """generate_from_template() must print SKIP and not raise if template absent."""
        monkeypatch.setattr(gc, "ROOT", str(tmp_path))
        gc.generate_from_template("nonexistent.yml.template", "output.yml", {"KEY": "val"})
        captured = capsys.readouterr()
        assert "SKIP" in captured.out

    def test_template_generates_output_file(self, tmp_path, monkeypatch):
        """A valid template file must produce an output file."""
        monkeypatch.setattr(gc, "ROOT", str(tmp_path))
        template_path = tmp_path / "test.yml.template"
        template_path.write_text("host: __REMOTE_IP__\nport: __LOKI_PORT__\n")
        gc.generate_from_template("test.yml.template", "test.yml", {
            "REMOTE_IP": "10.10.2.21",
            "LOKI_PORT": "3100",
        })
        output = (tmp_path / "test.yml").read_text()
        assert "10.10.2.21" in output
        assert "3100" in output
        assert "__REMOTE_IP__" not in output
        assert "__LOKI_PORT__" not in output
