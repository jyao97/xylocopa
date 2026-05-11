#!/bin/bash
# Regenerate the self-signed cert for xylocopa.
#
# Two modes:
#   ./ensure-cert.sh
#     Auto-detect non-loopback host IPs.  Idempotent: skips regen if the
#     SAN already covers every detected IP.
#
#   ./ensure-cert.sh --ips "ip1,ip2,ip3" [--dns "name1,name2"]
#     Force regen with the explicit IP/DNS list.  Always regenerates
#     (no SAN-coverage shortcut), used by /api/cert/regenerate after the
#     user edits the IP list on /cert-guide.
#
# mkcert preferred — leaf is re-signed by the existing CA so installed
# client trust survives.  openssl fallback creates a brand-new self-signed
# CA, forcing every client to reinstall via /cert-guide.
#
# NOT auto-invoked from run.sh — the user triggers it explicitly.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERTS_DIR="$ROOT/certs"
CERT="$CERTS_DIR/selfsigned.crt"
KEY="$CERTS_DIR/selfsigned.key"

EXPLICIT_IPS=""
EXPLICIT_DNS=""
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --ips)   EXPLICIT_IPS="$2"; FORCE=1; shift 2 ;;
        --dns)   EXPLICIT_DNS="$2"; FORCE=1; shift 2 ;;
        --force) FORCE=1; shift ;;
        *) echo "[cert] unknown arg: $1" >&2; exit 2 ;;
    esac
done

detect_ips() {
    hostname -I 2>/dev/null | tr ' ' '\n' \
        | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' \
        | grep -v '^127\.' \
        | sort -u
}

cert_san_ips() {
    openssl x509 -in "$CERT" -noout -ext subjectAltName 2>/dev/null \
        | grep -oE 'IP Address:[0-9.]+' | cut -d: -f2 | sort -u
}

regen_via_mkcert() {
    local args_csv="$1"
    local dns_csv="$2"
    local caroot
    caroot=$(mkcert -CAROOT 2>/dev/null) || return 1
    [ -f "$caroot/rootCA.pem" ] && [ -f "$caroot/rootCA-key.pem" ] || return 1

    local args=()
    declare -A seen=()
    add() { [ -z "$1" ] && return; [ -n "${seen[$1]:-}" ] && return; seen[$1]=1; args+=( "$1" ); }

    # DNS names first, then IPs — mkcert accepts mixed positional list
    if [ -n "$dns_csv" ]; then
        IFS=',' read -ra dns_arr <<< "$dns_csv"
        for d in "${dns_arr[@]}"; do add "$d"; done
    else
        add xylocopa; add localhost
    fi
    add 127.0.0.1
    IFS=',' read -ra ip_arr <<< "$args_csv"
    for ip in "${ip_arr[@]}"; do add "$ip"; done

    mkcert -cert-file "$CERT" -key-file "$KEY" "${args[@]}" >/dev/null 2>&1 || return 1
    cp "$caroot/rootCA.pem" "$CERTS_DIR/rootCA.pem" 2>/dev/null || true
    return 0
}

regen_via_openssl() {
    local ips_csv="$1"
    local dns_csv="$2"
    local san=""
    declare -A seen_dns=() seen_ip=()
    if [ -n "$dns_csv" ]; then
        IFS=',' read -ra dns_arr <<< "$dns_csv"
        for d in "${dns_arr[@]}"; do
            [ -z "$d" ] && continue
            [ -n "${seen_dns[$d]:-}" ] && continue
            seen_dns[$d]=1
            san="${san}${san:+,}DNS:${d}"
        done
    else
        san="DNS:xylocopa,DNS:localhost"
        seen_dns[xylocopa]=1; seen_dns[localhost]=1
    fi
    san="${san},IP:127.0.0.1"
    seen_ip[127.0.0.1]=1
    IFS=',' read -ra ip_arr <<< "$ips_csv"
    for ip in "${ip_arr[@]}"; do
        [ -z "$ip" ] && continue
        [ -n "${seen_ip[$ip]:-}" ] && continue
        seen_ip[$ip]=1
        san="${san},IP:${ip}"
    done

    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$KEY" -out "$CERT" \
        -subj "/CN=xylocopa" \
        -addext "subjectAltName=${san}" 2>/dev/null || return 1
    return 0
}

main() {
    local target_ips target_dns
    if [ -n "$EXPLICIT_IPS" ]; then
        target_ips="$EXPLICIT_IPS"
    else
        target_ips=$(detect_ips | paste -sd, -)
    fi
    target_dns="$EXPLICIT_DNS"

    if [ -z "$target_ips" ]; then
        echo "[cert] no IPs to cover — skipping"
        exit 0
    fi

    # Idempotent check (only when not forced and using auto-detect)
    if [ "$FORCE" = "0" ] && [ -f "$CERT" ]; then
        local existing missing=""
        existing=$(cert_san_ips)
        IFS=',' read -ra want_arr <<< "$target_ips"
        for ip in "${want_arr[@]}"; do
            if ! echo "$existing" | grep -qFx "$ip"; then
                missing="$missing $ip"
            fi
        done
        if [ -z "$missing" ]; then
            echo "[cert] SAN already covers all IPs — no regen needed"
            exit 0
        fi
        echo "[cert] IPs missing from SAN:${missing}"
    fi

    echo "[cert] regenerating leaf cert with IPs: $target_ips"
    [ -n "$target_dns" ] && echo "[cert]                          DNS: $target_dns"

    if command -v mkcert >/dev/null 2>&1 && regen_via_mkcert "$target_ips" "$target_dns"; then
        echo "[cert] regenerated via mkcert — CA unchanged, existing client trust survives"
    elif regen_via_openssl "$target_ips" "$target_dns"; then
        echo "[cert] regenerated via openssl — NEW self-signed CA, clients must reinstall"
    else
        echo "[cert] regen FAILED — leaving existing cert in place" >&2
        exit 1
    fi

    date +%s > "$CERTS_DIR/.last-regen" 2>/dev/null || true
}

main
