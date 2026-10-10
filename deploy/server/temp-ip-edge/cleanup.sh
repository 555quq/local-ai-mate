#!/usr/bin/env bash

set -uo pipefail

PROJECT_MARKER="luna-temp-edge-v2.3.2"
CERT_NAME="luna-temp-ip-49-234-57-214"
ROOT_PREFIX="${LUNA_TEMP_EDGE_TEST_ROOT:-}"
failures=0

require_test_binary() {
    local variable_name="$1"
    local value
    local resolved

    if [[ ! -v "${variable_name}" ]]; then
        printf '%s\n' "Missing required test command: ${variable_name}" >&2
        return 1
    fi
    value="${!variable_name}"
    if [[ -z "${value}" || ! -x "${value}" || -L "${value}" ]]; then
        printf '%s\n' "Unsafe test command: ${variable_name}" >&2
        return 1
    fi
    resolved="$(readlink -f -- "${value}")"
    if [[ $? -ne 0 ]]; then
        return 1
    fi
    case "${resolved}" in
        "${ROOT_PREFIX}"/mock-bin/*) printf '%s\n' "${resolved}" ;;
        *)
            printf '%s\n' "Test command escapes the isolated root: ${variable_name}" >&2
            return 1
            ;;
    esac
}

if [[ -n "${ROOT_PREFIX}" ]]; then
    if [[ ! -d "${ROOT_PREFIX}" || -L "${ROOT_PREFIX}" ]]; then
        printf '%s\n' "Test root must be an existing non-symlink directory" >&2
        exit 64
    fi
    resolved_root="$(readlink -f -- "${ROOT_PREFIX}")"
    if [[ $? -ne 0 || -z "${resolved_root}" ]]; then
        printf '%s\n' "Test root could not be resolved" >&2
        exit 64
    fi
    ROOT_PREFIX="${resolved_root}"
    case "${ROOT_PREFIX}" in
        /tmp/luna-temp-edge-test-*) ;;
        *)
            printf '%s\n' "Refusing unsafe test root" >&2
            exit 64
            ;;
    esac

    if ! SYSTEMCTL_BIN="$(require_test_binary LUNA_TEMP_EDGE_SYSTEMCTL_BIN)" \
        || ! SS_BIN="$(require_test_binary LUNA_TEMP_EDGE_SS_BIN)" \
        || ! UFW_BIN="$(require_test_binary LUNA_TEMP_EDGE_UFW_BIN)" \
        || ! SLEEP_BIN="$(require_test_binary LUNA_TEMP_EDGE_SLEEP_BIN)" \
        || ! CERTBOT_BIN="$(require_test_binary LUNA_TEMP_EDGE_CERTBOT_BIN)"; then
        exit 64
    fi
else
    SYSTEMCTL_BIN="/usr/bin/systemctl"
    SS_BIN="/usr/bin/ss"
    UFW_BIN="/usr/sbin/ufw"
    SLEEP_BIN="/usr/bin/sleep"
    CERTBOT_BIN="/snap/bin/certbot"
fi

UFW_OWNERSHIP_FILE="${ROOT_PREFIX}/var/lib/luna-temp-edge/ufw-rule-owned"
LOG_FILE="${ROOT_PREFIX}/var/log/luna-temp-edge-expiry.log"
mkdir -p -- "${ROOT_PREFIX}/var/log"

record() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >>"${LOG_FILE}" 2>/dev/null || true
}

unit_load_state() {
    "${SYSTEMCTL_BIN}" show --property=LoadState --value "$1" 2>/dev/null
}

ensure_unit_inactive() {
    local unit="$1"
    local load_state
    local status

    load_state="$(unit_load_state "${unit}")"
    status=$?
    if [[ "${status}" -ne 0 ]]; then
        record "ERROR unable to inspect unit load state"
        return 1
    fi
    if [[ "${load_state}" == "not-found" ]]; then
        return 0
    fi

    "${SYSTEMCTL_BIN}" is-active "${unit}" >/dev/null 2>&1
    status=$?
    case "${status}" in
        0)
            if ! "${SYSTEMCTL_BIN}" stop "${unit}" >/dev/null 2>&1; then
                record "ERROR failed to stop a temporary edge unit"
                return 1
            fi
            ;;
        3|4) ;;
        *)
            record "ERROR unable to determine unit activity"
            return 1
            ;;
    esac

    "${SYSTEMCTL_BIN}" is-active "${unit}" >/dev/null 2>&1
    status=$?
    case "${status}" in
        3|4) return 0 ;;
        *)
            record "ERROR temporary edge unit is not confirmed inactive"
            return 1
            ;;
    esac
}

ensure_unit_disabled() {
    local unit="$1"
    local load_state
    local enabled_state

    load_state="$(unit_load_state "${unit}")"
    if [[ $? -ne 0 ]]; then
        record "ERROR unable to inspect unit load state"
        return 1
    fi
    if [[ "${load_state}" == "not-found" ]]; then
        return 0
    fi

    enabled_state="$("${SYSTEMCTL_BIN}" is-enabled "${unit}" 2>/dev/null)"
    case "${enabled_state}" in
        enabled|enabled-runtime|linked|linked-runtime|alias)
            if ! "${SYSTEMCTL_BIN}" disable "${unit}" >/dev/null 2>&1; then
                record "ERROR failed to disable a temporary edge unit"
                return 1
            fi
            ;;
        disabled|static|indirect|masked|masked-runtime|generated|transient) ;;
        *)
            record "ERROR unable to determine unit enablement"
            return 1
            ;;
    esac

    enabled_state="$("${SYSTEMCTL_BIN}" is-enabled "${unit}" 2>/dev/null)"
    case "${enabled_state}" in
        disabled|static|indirect|masked|masked-runtime|generated|transient) return 0 ;;
        *)
            record "ERROR temporary edge unit is not confirmed disabled"
            return 1
            ;;
    esac
}

verify_port_closed_three_times() {
    local attempt
    local listeners

    for attempt in 1 2 3; do
        listeners="$("${SS_BIN}" -ltnH 'sport = :8443' 2>/dev/null)"
        if [[ $? -ne 0 ]]; then
            record "ERROR unable to verify port 8443"
            return 1
        fi
        if [[ -n "${listeners}" ]]; then
            record "ERROR port 8443 remains in use"
            return 1
        fi
        if [[ "${attempt}" -lt 3 ]]; then
            "${SLEEP_BIN}" 1
            if [[ $? -ne 0 ]]; then
                record "ERROR port verification delay failed"
                return 1
            fi
        fi
    done
}

remove_file() {
    if ! rm -f -- "$1"; then
        record "ERROR failed to remove a temporary edge file"
        failures=1
    fi
}

record "cleanup started"

# Revoke only a firewall rule that this deployment explicitly recorded as owned.
if [[ -f "${UFW_OWNERSHIP_FILE}" ]]; then
    ownership="$(<"${UFW_OWNERSHIP_FILE}")"
    if [[ "${ownership}" != "${PROJECT_MARKER}" ]]; then
        record "ERROR invalid UFW ownership record"
        failures=1
    else
        status_output="$("${UFW_BIN}" status 2>/dev/null)"
        if [[ $? -ne 0 ]]; then
            record "ERROR unable to inspect the owned UFW rule"
            failures=1
        elif grep -Fq "${PROJECT_MARKER}" <<<"${status_output}"; then
            if ! "${UFW_BIN}" --force delete allow 8443/tcp comment "${PROJECT_MARKER}" >/dev/null 2>&1; then
                record "ERROR failed to remove the owned UFW rule"
                failures=1
            else
                remove_file "${UFW_OWNERSHIP_FILE}"
            fi
        else
            remove_file "${UFW_OWNERSHIP_FILE}"
        fi
    fi
fi

# Do not touch credentials or certificates until shutdown has been proven.
ensure_unit_inactive luna-temp-edge.service
if [[ $? -ne 0 ]]; then
    exit 1
fi
ensure_unit_disabled luna-temp-edge.service
if [[ $? -ne 0 ]]; then
    exit 1
fi
verify_port_closed_three_times
if [[ $? -ne 0 ]]; then
    printf '%s\n' "Port 8443 shutdown could not be proven" >&2
    exit 1
fi

if [[ "${failures}" -ne 0 ]]; then
    record "firewall cleanup failed after edge shutdown; retry assets retained"
    exit 1
fi

remove_file "${ROOT_PREFIX}/etc/luna-temp-edge/htpasswd"

if [[ -e "${ROOT_PREFIX}/etc/letsencrypt/renewal/${CERT_NAME}.conf" \
    || -d "${ROOT_PREFIX}/etc/letsencrypt/live/${CERT_NAME}" \
    || -d "${ROOT_PREFIX}/etc/letsencrypt/archive/${CERT_NAME}" ]]; then
    if [[ ! -x "${CERTBOT_BIN}" ]] \
        || ! "${CERTBOT_BIN}" delete --non-interactive --cert-name "${CERT_NAME}" >/dev/null 2>&1; then
        record "ERROR failed to delete the dedicated certificate lineage"
        failures=1
    fi
fi

if ! rm -rf -- "${ROOT_PREFIX}/var/lib/luna-temp-edge/nginx"; then
    record "ERROR failed to remove the temporary Nginx state"
    failures=1
fi
if ! rm -rf -- "${ROOT_PREFIX}/var/lib/luna-temp-edge/acme"; then
    record "ERROR failed to remove the temporary ACME webroot"
    failures=1
fi
if ! rm -rf -- "${ROOT_PREFIX}/run/luna-temp-edge"; then
    record "ERROR failed to remove the temporary runtime directory"
    failures=1
fi
if ! rm -rf -- "${ROOT_PREFIX}/var/log/luna-temp-edge"; then
    record "ERROR failed to remove the temporary Nginx log directory"
    failures=1
fi
remove_file "${ROOT_PREFIX}/var/lib/luna-temp-edge/expires-at"
remove_file "${ROOT_PREFIX}/var/lib/luna-temp-edge/expires-at.test-security"
remove_file "${ROOT_PREFIX}/etc/luna-temp-edge/nginx.conf"
remove_file "${ROOT_PREFIX}/etc/luna-temp-edge/tls/fullchain.pem"
remove_file "${ROOT_PREFIX}/etc/luna-temp-edge/tls/privkey.pem"
rmdir -- "${ROOT_PREFIX}/etc/luna-temp-edge/tls" 2>/dev/null || true
rmdir -- "${ROOT_PREFIX}/var/lib/luna-temp-edge" 2>/dev/null || true
rmdir -- "${ROOT_PREFIX}/etc/luna-temp-edge" 2>/dev/null || true

# Any failed certificate or file cleanup keeps both persistent timers for retry.
if [[ "${failures}" -ne 0 ]]; then
    record "cleanup finished with warnings; timers and retry assets retained"
    exit 1
fi

ensure_unit_inactive luna-temp-edge-failsafe.timer
if [[ $? -ne 0 ]]; then
    exit 1
fi
ensure_unit_disabled luna-temp-edge-failsafe.timer
if [[ $? -ne 0 ]]; then
    exit 1
fi
ensure_unit_inactive luna-temp-edge-expiry.timer
if [[ $? -ne 0 ]]; then
    exit 1
fi
ensure_unit_disabled luna-temp-edge-expiry.timer
if [[ $? -ne 0 ]]; then
    exit 1
fi
ensure_unit_inactive luna-temp-edge-retry.timer
if [[ $? -ne 0 ]]; then
    exit 1
fi
ensure_unit_disabled luna-temp-edge-retry.timer
if [[ $? -ne 0 ]]; then
    exit 1
fi

remove_file "${ROOT_PREFIX}/etc/systemd/system/luna-temp-edge.service"
remove_file "${ROOT_PREFIX}/etc/systemd/system/luna-temp-edge-expiry.service"
remove_file "${ROOT_PREFIX}/etc/systemd/system/luna-temp-edge-expiry.timer"
remove_file "${ROOT_PREFIX}/etc/systemd/system/luna-temp-edge-failsafe.timer"
remove_file "${ROOT_PREFIX}/etc/systemd/system/luna-temp-edge-retry.timer"
if [[ "${failures}" -ne 0 ]]; then
    record "cleanup could not remove all units; retry assets retained"
    exit 1
fi

if ! "${SYSTEMCTL_BIN}" daemon-reload >/dev/null 2>&1; then
    record "ERROR systemd daemon reload failed; retry assets retained"
    exit 1
fi
"${SYSTEMCTL_BIN}" reset-failed luna-temp-edge.service luna-temp-edge-expiry.service >/dev/null 2>&1 || true

record "cleanup completed"
remove_file "${ROOT_PREFIX}/usr/local/lib/luna-temp-edge/check-expiry.sh"
remove_file "${ROOT_PREFIX}/usr/local/lib/luna-temp-edge/cleanup.sh"
rmdir -- "${ROOT_PREFIX}/usr/local/lib/luna-temp-edge" 2>/dev/null || true
exit "${failures}"
