#!/usr/bin/env bash

set -uo pipefail

ROOT_PREFIX="${LUNA_TEMP_EDGE_TEST_ROOT:-}"
EXPIRY_FILE="/var/lib/luna-temp-edge/expires-at"

fail_closed() {
    printf '%s\n' "$1" >&2
    exit 255
}

if [[ -n "${ROOT_PREFIX}" ]]; then
    if [[ ! -d "${ROOT_PREFIX}" || -L "${ROOT_PREFIX}" ]]; then
        fail_closed "Test root must be an existing non-symlink directory"
    fi
    resolved_root="$(readlink -f -- "${ROOT_PREFIX}")"
    if [[ $? -ne 0 || -z "${resolved_root}" ]]; then
        fail_closed "Test root could not be resolved"
    fi
    ROOT_PREFIX="${resolved_root}"
    case "${ROOT_PREFIX}" in
        /tmp/luna-temp-edge-test-*) ;;
        *) fail_closed "Refusing unsafe test root" ;;
    esac
    EXPIRY_FILE="${ROOT_PREFIX}${EXPIRY_FILE}"
    TEST_SECURITY_FILE="${EXPIRY_FILE}.test-security"
    if [[ ! -f "${TEST_SECURITY_FILE}" || -L "${TEST_SECURITY_FILE}" \
        || "$(<"${TEST_SECURITY_FILE}")" != "owner=test;mode=0600" ]]; then
        fail_closed "Test expiry security assertion is missing or invalid"
    fi
else
    if [[ ! -f "${EXPIRY_FILE}" || -L "${EXPIRY_FILE}" ]]; then
        fail_closed "Temporary edge expiry record is missing or unsafe"
    fi
    metadata="$(stat -c "%u:%a" "${EXPIRY_FILE}" 2>/dev/null)"
    if [[ $? -ne 0 || "${metadata}" != "0:600" ]]; then
        fail_closed "Temporary edge expiry record must be root-owned mode 0600"
    fi
fi

if [[ ! -f "${EXPIRY_FILE}" || -L "${EXPIRY_FILE}" ]]; then
    fail_closed "Temporary edge expiry record is missing or unsafe"
fi

{
    IFS= read -r expires_at \
        || fail_closed "Temporary edge expiry record must end with a newline"
    if IFS= read -r extra_line; then
        fail_closed "Temporary edge expiry record must contain exactly one line"
    fi
} <"${EXPIRY_FILE}"

if [[ ! "${expires_at}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    fail_closed "Temporary edge expiry must use strict UTC ISO format"
fi

normalized="$(date -u --date="${expires_at}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
if [[ $? -ne 0 || "${normalized}" != "${expires_at}" ]]; then
    fail_closed "Temporary edge expiry record is invalid"
fi

expiry_epoch="$(date -u --date="${expires_at}" +%s 2>/dev/null)"
if [[ $? -ne 0 || ! "${expiry_epoch}" =~ ^[0-9]+$ ]]; then
    fail_closed "Temporary edge expiry record is invalid"
fi

now_epoch="$(date -u +%s 2>/dev/null)"
if [[ $? -ne 0 || ! "${now_epoch}" =~ ^[0-9]+$ ]]; then
    fail_closed "Current UTC time could not be determined"
fi

# Exit 1 is an ordinary systemd ExecCondition skip; retry.timer checks again later.
if (( expiry_epoch > now_epoch )); then
    exit 1
fi

exit 0
