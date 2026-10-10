from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = ROOT / "deploy/server/temp-ip-edge"


def read(filename: str) -> str:
    return (ASSET_ROOT / filename).read_text(encoding="utf-8")


def test_nginx_is_a_complete_isolated_8443_configuration() -> None:
    config = read("nginx.conf.template")

    assert "worker_processes 1;" in config
    assert 'lua_package_path "/www/server/nginx/lib/lua/?.lua;;";' in config
    assert "pid /run/luna-temp-edge/nginx.pid;" in config
    assert "events {" in config
    assert "http {" in config
    assert "listen __LISTEN_ADDRESS__:8443 ssl;" in config
    assert "listen 0.0.0.0:8443" not in config
    assert "listen 127.0.0.1:8443" not in config
    assert not re.search(r"\blisten\s+(?:\[::\]:)?(?:80|443)\b", config)
    assert "root /www/wwwroot/luna.shipmaster.asia/current;" in config
    assert "proxy_pass http://127.0.0.1:18765;" in config
    assert "__PUBLIC_IP__" in config
    assert "__NGINX_BINARY__" not in config

    for path in (
        "/var/lib/luna-temp-edge/nginx/client_temp",
        "/var/lib/luna-temp-edge/nginx/proxy_temp",
        "/var/lib/luna-temp-edge/nginx/fastcgi_temp",
        "/var/lib/luna-temp-edge/nginx/uwsgi_temp",
        "/var/lib/luna-temp-edge/nginx/scgi_temp",
    ):
        assert path in config


def test_nginx_enforces_tls_auth_streaming_and_origin_contract() -> None:
    config = read("nginx.conf.template")

    assert "ssl_certificate __CERT_FULLCHAIN__;" in config
    assert "ssl_certificate_key __CERT_PRIVKEY__;" in config
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert 'auth_basic "Luna temporary test";' in config
    assert "auth_basic_user_file __TEMP_AUTH_FILE__;" in config
    assert "client_max_body_size 9m;" in config
    assert "proxy_read_timeout 180s;" in config
    assert "proxy_send_timeout 180s;" in config
    assert "proxy_buffering off;" in config
    assert "proxy_request_buffering off;" in config
    assert "proxy_cache off;" in config
    assert "proxy_set_header Origin https://luna.shipmaster.asia;" in config
    assert 'proxy_set_header Authorization "";' in config
    assert "proxy_set_header X-Session-Token $http_x_session_token;" in config
    assert "access_log off;" in config
    assert 'map $http_origin $luna_exact_origin_allowed {' in config
    assert '"https://__PUBLIC_IP__:8443" 1;' in config
    assert 'map "$request_method|$http_origin" $luna_empty_read_allowed {' in config
    assert '"~^(GET|HEAD|OPTIONS)\\|$" 1;' in config
    assert 'map "$luna_exact_origin_allowed|$luna_empty_read_allowed"' in config
    assert "same-origin 1;" in config
    assert "none 1;" in config
    assert "if ($luna_origin_allowed = 0)" in config
    assert "if ($luna_fetch_site_allowed = 0)" in config

    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        'Permissions-Policy "microphone=(self)"',
        "Content-Security-Policy",
    ):
        assert config.count(header) >= 2
    assert config.count("media-src 'self' blob: data:") >= 2


def test_expiry_guard_rejects_missing_invalid_or_elapsed_deadline() -> None:
    script = read("check-expiry.sh")

    assert 'EXPIRY_FILE="/var/lib/luna-temp-edge/expires-at"' in script
    assert '-f "${EXPIRY_FILE}"' in script
    assert "date -u --date=" in script
    assert "^[0-9]{4}-[0-9]{2}-[0-9]{2}T" in script
    assert 'stat -c "%u:%a"' in script
    assert '"0:600"' in script
    assert ".test-security" in script
    assert "expiry_epoch" in script
    assert "now_epoch" in script
    assert "expiry_epoch <= now_epoch" in script
    assert "exit 1" in script


def test_cleanup_due_guard_only_allows_cleanup_at_or_after_deadline() -> None:
    script = read("cleanup-due.sh")

    assert 'EXPIRY_FILE="/var/lib/luna-temp-edge/expires-at"' in script
    assert '-f "${EXPIRY_FILE}"' in script
    assert 'stat -c "%u:%a"' in script
    assert '"0:600"' in script
    assert ".test-security" in script
    assert "date -u --date=" in script
    assert "^[0-9]{4}-[0-9]{2}-[0-9]{2}T" in script
    assert "expiry_epoch" in script
    assert "now_epoch" in script
    assert "expiry_epoch > now_epoch" in script
    assert "exit 255" in script


def test_acme_template_exposes_only_the_challenge_path() -> None:
    config = read("acme-http.conf.template")

    assert "listen 80;" in config
    assert "server_name __PUBLIC_IP__;" in config
    assert "location ^~ /.well-known/acme-challenge/" in config
    assert "root /var/lib/luna-temp-edge/acme;" in config
    assert "try_files $uri =404;" in config
    assert re.search(r"location\s+/\s*\{\s*return 404;\s*\}", config, re.DOTALL)
    assert "proxy_pass" not in config
    assert "auth_basic" not in config
    assert not re.search(r"\blisten\s+(?:\[::\]:)?(?:443|8443)\b", config)


def test_systemd_units_validate_and_run_the_isolated_edge() -> None:
    edge = read("luna-temp-edge.service.template")
    expiry = read("luna-temp-edge-expiry.service.template")
    timer = read("luna-temp-edge-expiry.timer.template")
    failsafe = read("luna-temp-edge-failsafe.timer.template")
    retry = read("luna-temp-edge-retry.timer.template")

    command = (
        "__NGINX_BINARY__ -p /var/lib/luna-temp-edge/nginx/ "
        "-c /etc/luna-temp-edge/nginx.conf"
    )
    assert f"ExecStartPre={command} -t" in edge
    assert f'ExecStart={command} -g "daemon off;"' in edge
    assert "ExecCondition=+/usr/local/lib/luna-temp-edge/check-expiry.sh" in edge
    assert "PIDFile=/run/luna-temp-edge/nginx.pid" in edge
    assert "User=__NGINX_USER__" in edge
    assert "Group=__NGINX_USER__" in edge
    assert "ProtectSystem=strict" in edge
    assert "ReadOnlyPaths=/etc/luna-temp-edge" in edge
    assert "ReadOnlyPaths=/www/wwwroot/luna.shipmaster.asia/current" in edge
    assert "ReadWritePaths=/run/luna-temp-edge" in edge
    assert "ReadWritePaths=/var/log/luna-temp-edge" in edge
    assert "ReadWritePaths=/var/lib/luna-temp-edge/nginx" in edge
    assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX" in edge
    assert "CapabilityBoundingSet=" in edge
    assert "ExecCondition=/usr/local/lib/luna-temp-edge/cleanup-due.sh" in expiry
    assert "ExecStart=/usr/local/lib/luna-temp-edge/cleanup.sh" in expiry
    assert "Restart=on-failure" in expiry
    assert "RestartSec=" in expiry
    assert "OnCalendar=__EXPIRES_AT_UTC__" in timer
    assert "Persistent=true" in timer
    assert "Unit=luna-temp-edge-expiry.service" in timer
    assert "OnCalendar=__FAILSAFE_AT_UTC__" in failsafe
    assert "Persistent=true" in failsafe
    assert "Unit=luna-temp-edge-expiry.service" in failsafe
    assert "OnBootSec=1min" in retry
    assert "OnUnitInactiveSec=1min" in retry
    assert "Unit=luna-temp-edge-expiry.service" in retry
    assert "Persistent=" not in retry


def test_cleanup_is_fixed_scope_stop_first_and_idempotent() -> None:
    script = read("cleanup.sh")

    stop_at = script.index("\nensure_unit_inactive luna-temp-edge.service\n")
    disable_at = script.index("\nensure_unit_disabled luna-temp-edge.service\n")
    listen_check_at = script.index("\nverify_port_closed_three_times\n")
    auth_remove_at = script.index("/etc/luna-temp-edge/htpasswd")
    assert stop_at < disable_at < listen_check_at < auth_remove_at

    assert '"${SYSTEMCTL_BIN}" stop "${unit}"' in script
    assert '"${SYSTEMCTL_BIN}" disable "${unit}"' in script
    assert '"${SYSTEMCTL_BIN}" is-active "${unit}"' in script
    assert '"${SYSTEMCTL_BIN}" is-enabled "${unit}"' in script
    assert '"${SYSTEMCTL_BIN}" stop "${unit}"' + " || true" not in script
    assert '"${SYSTEMCTL_BIN}" disable "${unit}"' + " || true" not in script
    assert "for attempt in 1 2 3" in script
    failsafe_stop_at = script.index("ensure_unit_inactive luna-temp-edge-failsafe.timer")
    expiry_stop_at = script.index("ensure_unit_inactive luna-temp-edge-expiry.timer")
    retry_stop_at = script.index("ensure_unit_inactive luna-temp-edge-retry.timer")
    expiry_record_remove_at = script.index(
        'terminal_remove_file "${ROOT_PREFIX}/var/lib/luna-temp-edge/expires-at"'
    )
    assert 'UFW_OWNERSHIP_FILE="${ROOT_PREFIX}/var/lib/luna-temp-edge/ufw-rule-owned"' in script
    ownership_at = script.index('[[ -f "${UFW_OWNERSHIP_FILE}" ]]')
    ufw_delete_at = script.index('"${UFW_BIN}" --force delete allow 8443/tcp')
    assert ownership_at < ufw_delete_at < stop_at
    firewall_gate = script.index("firewall cleanup failed after edge shutdown")
    assert ufw_delete_at < stop_at < listen_check_at < firewall_gate < auth_remove_at
    final_failure_gate = script.index("timers and retry assets retained")
    assert final_failure_gate < failsafe_stop_at
    assert final_failure_gate < expiry_stop_at
    assert final_failure_gate < retry_stop_at
    assert retry_stop_at < expiry_record_remove_at
    assert "terminal best-effort warning" in script
    assert "daemon reload failed; retry assets retained" not in script
    assert 'status_output="$("${UFW_BIN}" status' in script
    assert "invalid UFW ownership record" in script

    for variable in (
        "LUNA_TEMP_EDGE_SYSTEMCTL_BIN",
        "LUNA_TEMP_EDGE_SS_BIN",
        "LUNA_TEMP_EDGE_UFW_BIN",
        "LUNA_TEMP_EDGE_SLEEP_BIN",
        "LUNA_TEMP_EDGE_CERTBOT_BIN",
    ):
        assert variable in script

    assert "luna-temp-edge-v2.3.2" in script
    assert "luna-temp-ip-49-234-57-214" in script
    assert "/var/lib/luna-temp-edge/nginx" in script
    assert "/var/log/luna-temp-edge" in script
    assert "/etc/systemd/system/luna-temp-edge.service" in script
    assert "/etc/systemd/system/luna-temp-edge-expiry.service" in script
    assert "/etc/systemd/system/luna-temp-edge-expiry.timer" in script
    assert "/etc/systemd/system/luna-temp-edge-retry.timer" in script
    assert "/usr/local/lib/luna-temp-edge/cleanup.sh" in script
    assert "/usr/local/lib/luna-temp-edge/check-expiry.sh" in script
    assert "/usr/local/lib/luna-temp-edge/cleanup-due.sh" in script
    assert "LUNA_TEMP_EDGE_TEST_ROOT" in script
    assert "/tmp/luna-temp-edge-test-" in script

    assert "rm -rf /etc/letsencrypt" not in script
    assert "rm -rf \"/etc/letsencrypt" not in script
    assert 'rm -rf -- "$1"' not in script
    assert not re.search(r"rm\s+-rf\s+(?:--\s+)?\$(?!\{ROOT_PREFIX\})", script)
    assert "systemctl stop --all" not in script
    assert "ufw reset" not in script
    assert final_failure_gate < script.index(
        'remove_file "${ROOT_PREFIX}/usr/local/lib/luna-temp-edge/cleanup.sh"'
    )


def test_assets_contain_placeholders_and_no_embedded_secrets() -> None:
    filenames = (
        "acme-http.conf.template",
        "nginx.conf.template",
        "luna-temp-edge.service.template",
        "luna-temp-edge-expiry.service.template",
        "luna-temp-edge-expiry.timer.template",
        "luna-temp-edge-failsafe.timer.template",
        "luna-temp-edge-retry.timer.template",
        "cleanup.sh",
        "check-expiry.sh",
        "cleanup-due.sh",
    )
    combined = "\n".join(read(filename) for filename in filenames)

    for placeholder in (
        "__PUBLIC_IP__",
        "__LISTEN_ADDRESS__",
        "__NGINX_BINARY__",
        "__NGINX_USER__",
        "__CERT_FULLCHAIN__",
        "__CERT_PRIVKEY__",
        "__TEMP_AUTH_FILE__",
        "__EXPIRES_AT_UTC__",
        "__FAILSAFE_AT_UTC__",
    ):
        assert placeholder in combined

    forbidden = (
        "X-Session-Token: ",
        "BEGIN PRIVATE KEY",
        "BEGIN OPENSSH PRIVATE KEY",
        "Basic dGV",
        "password=",
    )
    for marker in forbidden:
        assert marker not in combined
