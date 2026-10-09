from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_nginx_templates_protect_static_and_api() -> None:
    for filename in (
        "deploy/server/nginx-luna-http.conf.template",
        "deploy/server/nginx-luna-https.conf.template",
    ):
        config = read(filename)
        assert 'auth_basic "Local AI Mate";' in config
        assert "auth_basic_user_file /www/server/passwd/luna.shipmaster.asia.htpasswd;" in config
        assert "location /api/" in config
        assert "proxy_pass http://127.0.0.1:18765;" in config
        assert "proxy_buffering off;" in config
        assert "proxy_read_timeout 180s;" in config
        assert "proxy_send_timeout 180s;" in config
        assert "client_max_body_size 9m;" in config


def test_https_template_has_required_browser_security_headers() -> None:
    config = read("deploy/server/nginx-luna-https.conf.template")
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert "Strict-Transport-Security" in config
    assert 'Permissions-Policy "microphone=(self)"' in config
    assert "Content-Security-Policy" in config
    assert "return 301 https://$host$request_uri;" in config


def test_frp_templates_keep_application_port_private() -> None:
    server = read("deploy/server/frps.toml.template")
    client = read("deploy/windows/frpc.toml.template")
    assert 'proxyBindAddr = "127.0.0.1"' in server
    assert "transport.tls.force = true" in server
    assert "{ single = 18765 }" in server
    assert 'auth.token = "__FRP_TOKEN__"' in server
    assert "transport.tls.enable = true" in client
    assert 'localIP = "127.0.0.1"' in client
    assert "localPort = 8765" in client
    assert "remotePort = 18765" in client
    assert 'auth.token = "__FRP_TOKEN__"' in client


def test_tracked_deployment_files_contain_placeholders_not_secrets() -> None:
    paths = [
        "deploy/server/frps.toml.template",
        "deploy/windows/frpc.toml.template",
        "deploy/windows/secrets.psd1.example",
        "frontend/.env.production.example",
    ]
    combined = "\n".join(read(path) for path in paths)
    assert "__FRP_TOKEN__" in combined
    assert "replace-with" in combined
    assert "49.234.57.214" not in combined
    assert "luna.shipmaster.asia" in combined
