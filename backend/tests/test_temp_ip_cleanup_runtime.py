from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = ROOT / "deploy/server/temp-ip-edge"


def find_real_bash() -> Path | None:
    candidates = [Path(r"C:\Git\bin\bash.exe")]
    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parent.parent
        candidates.extend((git_root / "bin/bash.exe", git_root / "usr/bin/bash.exe"))
    discovered = shutil.which("bash")
    if discovered:
        candidates.append(Path(discovered))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and "GNU bash" in probe.stdout:
            return candidate
    return None


def bash_output(bash: Path, command: str, *arguments: str) -> str:
    result = subprocess.run(
        [str(bash), "--noprofile", "--norc", "-c", command, "test", *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class CleanupHarness:
    def __init__(self, bash: Path, root_posix: str, root_windows: Path) -> None:
        self.bash = bash
        self.root_posix = root_posix
        self.root_windows = root_windows
        self.mock_bin = root_windows / "mock-bin"
        self.mock_state = root_windows / "mock-state"
        self.trace = self.mock_state / "trace"
        self.mock_bin.mkdir(parents=True)
        self.mock_state.mkdir(parents=True)
        self._install_mocks()
        self.populate()

    def path(self, relative: str) -> Path:
        return self.root_windows / Path(relative)

    def write(self, relative: str, content: str = "x\n") -> Path:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def posix(self, path: Path) -> str:
        return bash_output(self.bash, 'cygpath -u "$1"', str(path))

    def _install_mocks(self) -> None:
        systemctl = r'''#!/usr/bin/env bash
set -u
printf 'systemctl %s\n' "$*" >>"${MOCK_TRACE}"
command_name="$1"
shift
unit="${*: -1}"
case "${command_name}" in
    show)
        if [[ -f "${MOCK_ROOT}/etc/systemd/system/${unit}" ]]; then
            printf '%s\n' loaded
        else
            printf '%s\n' not-found
        fi
        ;;
    is-active)
        state="$(cat "${MOCK_STATE}/${unit}.active" 2>/dev/null || printf inactive)"
        printf '%s\n' "${state}"
        if [[ "${state}" == active ]]; then
            exit 0
        fi
        exit 3
        ;;
    stop)
        if [[ "${unit}" == luna-temp-edge.service && -f "${MOCK_STATE}/stop-fail" ]]; then
            exit 1
        fi
        printf '%s\n' inactive >"${MOCK_STATE}/${unit}.active"
        ;;
    is-enabled)
        state="$(cat "${MOCK_STATE}/${unit}.enabled" 2>/dev/null || printf disabled)"
        printf '%s\n' "${state}"
        [[ "${state}" == enabled ]]
        ;;
    disable)
        printf '%s\n' disabled >"${MOCK_STATE}/${unit}.enabled"
        ;;
    daemon-reload|reset-failed) ;;
    *) exit 2 ;;
esac
'''
        ss = r'''#!/usr/bin/env bash
set -u
counter_file="${MOCK_STATE}/ss-count"
count="$(cat "${counter_file}" 2>/dev/null || printf 0)"
count=$((count + 1))
printf '%s\n' "${count}" >"${counter_file}"
printf 'ss %s\n' "${count}" >>"${MOCK_TRACE}"
mode="$(cat "${MOCK_STATE}/ss-mode" 2>/dev/null || true)"
if [[ "${mode}" == always-listening || ( "${mode}" == reappear && "${count}" -ge 3 ) ]]; then
    printf '%s\n' 'LISTEN 0 511 0.0.0.0:8443 0.0.0.0:*'
fi
'''
        ufw = r'''#!/usr/bin/env bash
set -u
printf 'ufw %s\n' "$*" >>"${MOCK_TRACE}"
if [[ "$1" == status ]]; then
    if [[ -f "${MOCK_STATE}/ufw-status-fail" ]]; then
        exit 2
    fi
    if [[ -f "${MOCK_STATE}/ufw-present" ]]; then
        printf '%s\n' '8443/tcp ALLOW Anywhere # luna-temp-edge-v2.3.2'
    fi
    exit 0
fi
if [[ "$1" == --force && "$2" == delete ]]; then
    rm -f -- "${MOCK_STATE}/ufw-present"
    exit 0
fi
exit 2
'''
        certbot = r'''#!/usr/bin/env bash
set -u
printf 'certbot %s\n' "$*" >>"${MOCK_TRACE}"
if [[ -f "${MOCK_STATE}/certbot-fail" ]]; then
    exit 1
fi
rm -rf -- "${MOCK_ROOT}/etc/letsencrypt/live/luna-temp-ip-49-234-57-214"
rm -rf -- "${MOCK_ROOT}/etc/letsencrypt/archive/luna-temp-ip-49-234-57-214"
rm -f -- "${MOCK_ROOT}/etc/letsencrypt/renewal/luna-temp-ip-49-234-57-214.conf"
'''
        sleep = r'''#!/usr/bin/env bash
printf 'sleep %s\n' "$*" >>"${MOCK_TRACE}"
'''
        for name, content in {
            "systemctl": systemctl,
            "ss": ss,
            "ufw": ufw,
            "certbot": certbot,
            "sleep": sleep,
        }.items():
            self.write(f"mock-bin/{name}", content)

        mock_paths = [self.posix(path) for path in self.mock_bin.iterdir()]
        bash_output(
            self.bash,
            'chmod +x "$@"',
            *mock_paths,
        )

    def populate(self) -> None:
        for unit in (
            "luna-temp-edge.service",
            "luna-temp-edge-expiry.service",
            "luna-temp-edge-expiry.timer",
            "luna-temp-edge-failsafe.timer",
        ):
            self.write(f"etc/systemd/system/{unit}")
            self.write(f"mock-state/{unit}.active", "active\n")
            self.write(f"mock-state/{unit}.enabled", "enabled\n")

        self.write("etc/luna-temp-edge/htpasswd")
        self.write("etc/luna-temp-edge/nginx.conf")
        self.write("etc/luna-temp-edge/tls/fullchain.pem")
        self.write("etc/luna-temp-edge/tls/privkey.pem")
        self.write("var/lib/luna-temp-edge/nginx/client_temp/placeholder")
        self.write("var/lib/luna-temp-edge/acme/placeholder")
        self.write("var/lib/luna-temp-edge/expires-at", "2099-01-01T00:00:00Z\n")
        self.write(
            "usr/local/lib/luna-temp-edge/cleanup.sh",
            (ASSET_ROOT / "cleanup.sh").read_text(encoding="utf-8"),
        )
        self.write(
            "usr/local/lib/luna-temp-edge/check-expiry.sh",
            (ASSET_ROOT / "check-expiry.sh").read_text(encoding="utf-8"),
        )

    def env(self) -> dict[str, str]:
        values = os.environ.copy()
        values.update(
            {
                "LUNA_TEMP_EDGE_TEST_ROOT": self.root_posix,
                "LUNA_TEMP_EDGE_SYSTEMCTL_BIN": f"{self.root_posix}/mock-bin/systemctl",
                "LUNA_TEMP_EDGE_SS_BIN": f"{self.root_posix}/mock-bin/ss",
                "LUNA_TEMP_EDGE_UFW_BIN": f"{self.root_posix}/mock-bin/ufw",
                "LUNA_TEMP_EDGE_SLEEP_BIN": f"{self.root_posix}/mock-bin/sleep",
                "LUNA_TEMP_EDGE_CERTBOT_BIN": f"{self.root_posix}/mock-bin/certbot",
                "MOCK_ROOT": self.root_posix,
                "MOCK_STATE": f"{self.root_posix}/mock-state",
                "MOCK_TRACE": f"{self.root_posix}/mock-state/trace",
            }
        )
        return values

    def run(self, *, omit_env: str | None = None) -> subprocess.CompletedProcess[str]:
        environment = self.env()
        if omit_env:
            environment.pop(omit_env)
        script = self.posix(ASSET_ROOT / "cleanup.sh")
        return subprocess.run(
            [
                str(self.bash),
                "--noprofile",
                "--norc",
                "-c",
                'PATH=/usr/bin:/bin; export PATH; exec "$1"',
                "cleanup-test",
                script,
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

    def diagnostics(self) -> str:
        log = self.path("var/log/luna-temp-edge-expiry.log")
        return "\n".join(
            (
                f"trace={self.trace.read_text(encoding='utf-8') if self.trace.exists() else '<missing>'}",
                f"log={log.read_text(encoding='utf-8') if log.exists() else '<missing>'}",
            )
        )


@pytest.fixture
def cleanup_harness() -> CleanupHarness:
    bash = find_real_bash()
    if bash is None:
        pytest.skip("A real GNU Bash runtime is unavailable on this workstation")
    root_posix = bash_output(bash, "mktemp -d /tmp/luna-temp-edge-test-XXXXXX")
    root_windows = Path(bash_output(bash, 'cygpath -w "$1"', root_posix))
    harness = CleanupHarness(bash, root_posix, root_windows)
    try:
        yield harness
    finally:
        shutil.rmtree(root_windows, ignore_errors=True)


def test_shell_assets_parse_with_real_bash(cleanup_harness: CleanupHarness) -> None:
    for filename in ("cleanup.sh", "check-expiry.sh"):
        result = subprocess.run(
            [
                str(cleanup_harness.bash),
                "-n",
                cleanup_harness.posix(ASSET_ROOT / filename),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_cleanup_is_idempotent_and_stops_an_active_edge(
    cleanup_harness: CleanupHarness,
) -> None:
    cleanup_harness.write(
        "var/lib/luna-temp-edge/ufw-rule-owned", "luna-temp-edge-v2.3.2\n"
    )
    cleanup_harness.write("mock-state/ufw-present")

    first = cleanup_harness.run()
    second = cleanup_harness.run()

    assert first.returncode == 0, f"{first.stderr}\n{cleanup_harness.diagnostics()}"
    assert second.returncode == 0, f"{second.stderr}\n{cleanup_harness.diagnostics()}"
    assert not cleanup_harness.path("etc/luna-temp-edge/htpasswd").exists()
    assert not cleanup_harness.path("etc/systemd/system/luna-temp-edge.service").exists()
    trace = cleanup_harness.trace.read_text(encoding="utf-8")
    assert trace.index("ufw status") < trace.index("systemctl stop luna-temp-edge.service")
    assert trace.count("ss ") >= 3


def test_cleanup_stop_failure_keeps_credentials_and_retry_assets(
    cleanup_harness: CleanupHarness,
) -> None:
    cleanup_harness.write("mock-state/stop-fail")

    result = cleanup_harness.run()

    assert result.returncode != 0
    assert cleanup_harness.path("etc/luna-temp-edge/htpasswd").exists()
    assert cleanup_harness.path("etc/systemd/system/luna-temp-edge.service").exists()
    assert cleanup_harness.path("usr/local/lib/luna-temp-edge/cleanup.sh").exists()


def test_cleanup_rejects_a_listener_that_reappears(
    cleanup_harness: CleanupHarness,
) -> None:
    cleanup_harness.write("mock-state/ss-mode", "reappear\n")

    result = cleanup_harness.run()

    assert result.returncode != 0
    assert cleanup_harness.path("etc/luna-temp-edge/htpasswd").exists()
    assert cleanup_harness.path("etc/systemd/system/luna-temp-edge-expiry.timer").exists()


def test_certificate_failure_retains_both_persistent_timers_and_retry_assets(
    cleanup_harness: CleanupHarness,
) -> None:
    cleanup_harness.write(
        "etc/letsencrypt/renewal/luna-temp-ip-49-234-57-214.conf"
    )
    cleanup_harness.write(
        "etc/letsencrypt/live/luna-temp-ip-49-234-57-214/fullchain.pem"
    )
    cleanup_harness.write("mock-state/certbot-fail")

    result = cleanup_harness.run()

    assert result.returncode != 0
    for timer in ("luna-temp-edge-expiry.timer", "luna-temp-edge-failsafe.timer"):
        assert cleanup_harness.path(f"etc/systemd/system/{timer}").exists()
        assert cleanup_harness.path(f"mock-state/{timer}.active").read_text(
            encoding="utf-8"
        ).strip() == "active"
        assert cleanup_harness.path(f"mock-state/{timer}.enabled").read_text(
            encoding="utf-8"
        ).strip() == "enabled"
    assert cleanup_harness.path("usr/local/lib/luna-temp-edge/cleanup.sh").exists()


def test_ufw_ownership_mismatch_gates_all_destructive_cleanup(
    cleanup_harness: CleanupHarness,
) -> None:
    cleanup_harness.write("var/lib/luna-temp-edge/ufw-rule-owned", "someone-else\n")
    cleanup_harness.write("mock-state/ufw-present")

    result = cleanup_harness.run()

    assert result.returncode != 0
    assert cleanup_harness.path("etc/luna-temp-edge/htpasswd").exists()
    assert cleanup_harness.path("mock-state/ufw-present").exists()
    trace = cleanup_harness.trace.read_text(encoding="utf-8") if cleanup_harness.trace.exists() else ""
    assert "ufw --force delete" not in trace


def test_ufw_status_failure_retains_ownership_and_credentials(
    cleanup_harness: CleanupHarness,
) -> None:
    cleanup_harness.write(
        "var/lib/luna-temp-edge/ufw-rule-owned", "luna-temp-edge-v2.3.2\n"
    )
    cleanup_harness.write("mock-state/ufw-status-fail")

    result = cleanup_harness.run()

    assert result.returncode != 0
    assert cleanup_harness.path("var/lib/luna-temp-edge/ufw-rule-owned").exists()
    assert cleanup_harness.path("etc/luna-temp-edge/htpasswd").exists()


def test_test_root_requires_every_explicit_mock(cleanup_harness: CleanupHarness) -> None:
    result = cleanup_harness.run(omit_env="LUNA_TEMP_EDGE_SS_BIN")

    assert result.returncode == 64
    assert cleanup_harness.path("etc/luna-temp-edge/htpasswd").exists()


def test_expiry_guard_enforces_iso_utc_and_test_security_marker(
    cleanup_harness: CleanupHarness,
) -> None:
    expiry = cleanup_harness.path("var/lib/luna-temp-edge/expires-at")
    security = cleanup_harness.write(
        "var/lib/luna-temp-edge/expires-at.test-security", "owner=test;mode=0600\n"
    )
    script = cleanup_harness.posix(ASSET_ROOT / "check-expiry.sh")

    def run_guard() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(cleanup_harness.bash), script],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "LUNA_TEMP_EDGE_TEST_ROOT": cleanup_harness.root_posix,
            },
            check=False,
        )

    valid = run_guard()
    assert valid.returncode == 0, valid.stderr

    expiry.write_text("2099-01-01T00:00:00+00:00\n", encoding="utf-8", newline="\n")
    assert run_guard().returncode != 0

    expiry.write_text("2000-01-01T00:00:00Z\n", encoding="utf-8", newline="\n")
    assert run_guard().returncode != 0

    security.unlink()
    expiry.write_text("2099-01-01T00:00:00Z\n", encoding="utf-8", newline="\n")
    assert run_guard().returncode != 0
