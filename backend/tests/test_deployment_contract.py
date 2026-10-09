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


def test_ssh_templates_restrict_tunnel_to_loopback_remote_forward() -> None:
    sshd = read("deploy/server/sshd-luna-tunnel.conf.template")
    authorized_keys = read("deploy/server/authorized_keys.template")
    assert "Match User luna-tunnel" in sshd
    assert "PasswordAuthentication no" in sshd
    assert "KbdInteractiveAuthentication no" in sshd
    assert "AuthenticationMethods publickey" in sshd
    assert "AllowTcpForwarding remote" in sshd
    assert "GatewayPorts no" in sshd
    assert "PermitTTY no" in sshd
    assert "AllowAgentForwarding no" in sshd
    assert "restrict,port-forwarding" in authorized_keys
    assert 'permitlisten="127.0.0.1:18765"' in authorized_keys
    assert "__SSH_PUBLIC_KEY__" in authorized_keys


def test_frp_deployment_assets_are_removed() -> None:
    assert not (ROOT / "deploy/server/frps.toml.template").exists()
    assert not (ROOT / "deploy/server/frps.service").exists()
    assert not (ROOT / "deploy/windows/frpc.toml.template").exists()


def test_tracked_deployment_files_contain_placeholders_not_secrets() -> None:
    paths = [
        "deploy/server/sshd-luna-tunnel.conf.template",
        "deploy/server/authorized_keys.template",
        "deploy/windows/secrets.psd1.example",
        "frontend/.env.production.example",
    ]
    combined = "\n".join(read(path) for path in paths)
    assert "__SSH_PUBLIC_KEY__" in combined
    assert "replace-with" in combined
    assert "49.234.57.214" not in combined
    assert "luna.shipmaster.asia" in combined
    assert "FrpToken" not in combined
