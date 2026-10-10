#!/usr/bin/env bash

set -uo pipefail

ROOT_PREFIX="${LUNA_TEMP_EDGE_TEST_ROOT:-}"
EXPIRY_FILE="/var/lib/luna-temp-edge/expires-at"

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
    EXPIRY_FILE="${ROOT_PREFIX}${EXPIRY_FILE}"
    TEST_SECURITY_FILE="${EXPIRY_FILE}.test-security"
    if [[ ! -f "${TEST_SECURITY_FILE}" || -L "${TEST_SECURITY_FILE}" \
        || "$(<"${TEST_SECURITY_FILE}")" != "owner=test;mode=0600" ]]; then
        printf '%s\n' "Test expiry security assertion is missing or invalid" >&2
        exit 1
    fi
else
    if [[ ! -f "${EXPIRY_FILE}" || -L "${EXPIRY_FILE}" ]]; then
        printf '%s\n' "Temporary edge expiry record is missing or unsafe" >&2
        exit 1
    fi
    metadata="$(stat -c "%u:%a" "${EXPIRY_FILE}" 2>/dev/null)"
    if [[ $? -ne 0 || "${metadata}" != "0:600" ]]; then
        printf '%s\n' "Temporary edge expiry record must be root-owned mode 0600" >&2
        exit 1
    fi
fi

if [[ ! -f "${EXPIRY_FILE}" || -L "${EXPIRY_FILE}" ]]; then
    printf '%s\n' "Temporary edge expiry record is missing or unsafe" >&2
    exit 1
fi

{
    IFS= read -r expires_at || {
        printf '%s\n' "Temporary edge expiry record must end with a newline" >&2
        exit 1
    }
    if IFS= read -r extra_line; then
        printf '%s\n' "Temporary edge expiry record must contain exactly one line" >&2
        exit 1
    fi
} <"${EXPIRY_FILE}"

if [[ ! "${expires_at}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    printf '%s\n' "Temporary edge expiry must use strict UTC ISO format" >&2
    exit 1
fi

normalized="$(date -u --date="${expires_at}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
if [[ $? -ne 0 || "${normalized}" != "${expires_at}" ]]; then
    printf '%s\n' "Temporary edge expiry record is invalid" >&2
    exit 1
fi

expiry_epoch="$(date -u --date="${expires_at}" +%s 2>/dev/null)"
if [[ $? -ne 0 || ! "${expiry_epoch}" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "Temporary edge expiry record is invalid" >&2
    exit 1
fi

now_epoch="$(date -u +%s)"
if (( expiry_epoch <= now_epoch )); then
    printf '%s\n' "Temporary edge expiry has elapsed" >&2
    exit 1
fi

exit 0
