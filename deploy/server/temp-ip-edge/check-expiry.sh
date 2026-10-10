#!/usr/bin/env bash

set -uo pipefail

EXPIRY_FILE="/var/lib/luna-temp-edge/expires-at"

if [[ ! -f "${EXPIRY_FILE}" || -L "${EXPIRY_FILE}" ]]; then
    printf '%s\n' "Temporary edge expiry record is missing or unsafe" >&2
    exit 1
fi

IFS= read -r expires_at <"${EXPIRY_FILE}"
if [[ -z "${expires_at}" ]]; then
    printf '%s\n' "Temporary edge expiry record is empty" >&2
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
