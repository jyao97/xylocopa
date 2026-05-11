#!/bin/bash
# Auto-regenerate the self-signed cert if current host IPs aren't all in the
# cert SAN.  Called from run.sh on every start/restart so a Tailscale reinstall
# or LAN-IP change doesn't leave clients with a "Not secure" warning.
#
# mkcert preferred — leaf cert is re-signed by the same CA that's already
# installed on client devices, so existing client trust survives the regen.
# openssl fallback — the cert IS the CA, so regen invalidates client trust;
# users have to reinstall via /cert-guide.
#
# Idempotent: exits 0 if the SAN already covers every current IP.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERTS_DIR="$ROOT/certs"
CERT="$CERTS_DIR/selfsigned.crt"
KEY="$CERTS_DIR/selfsigned.key"

[ -f "$CERT" ] && [ -f "$KEY" ] || { echo "[cert] no cert at $CERT — install.js handles initial gen, skipping check"; exit 0; }

# All non-loopback IPv4 addresses on this host (LAN + Tailscale + Docker
# bridges all show up here since they're real network interfaces).
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
    local ips_csv="$1"
    local caroot
    caroot=$(mkcert -CAROOT 2>/dev/null) || return 1
    [ -f "$caroot/rootCA.pem" ] && [ -f "$caroot/rootCA-key.pem" ] || return 1

    # mkcert takes hostnames + IPs as positional args
    local args=( xylocopa localhost 127.0.0.1 )
    for ip in $ips_csv; do args+=( "$ip" ); done
    mkcert -cert-file "$CERT" -key-file "$KEY" "${args[@]}" >/dev/null 2>&1 || return 1

    # Mirror the CA into certs/ so /api/cert can serve it from the install dir
    # (the endpoint also checks ~/.local/share/mkcert as a fallback)
    cp "$caroot/rootCA.pem" "$CERTS_DIR/rootCA.pem" 2>/dev/null || true
    return 0
}

regen_via_openssl() {
    local ips="$1"
    local san="DNS:xylocopa,DNS:localhost,IP:127.0.0.1"
    for ip in $ips; do san="${san},IP:${ip}"; done
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$KEY" -out "$CERT" \
        -subj "/CN=xylocopa" \
        -addext "subjectAltName=${san}" 2>/dev/null || return 1
    return 0
}

main() {
    local current_ips cert_ips missing=""
    current_ips=$(detect_ips)
    if [ -z "$current_ips" ]; then
        echo "[cert] no non-loopback IPs detected — skipping check"
        exit 0
    fi

    cert_ips=$(cert_san_ips)
    for ip in $current_ips; do
        if ! echo "$cert_ips" | grep -qFx "$ip"; then
            missing="$missing $ip"
        fi
    done

    if [ -z "$missing" ]; then
        echo "[cert] SAN covers all current IPs — no regen needed"
        exit 0
    fi

    echo "[cert] new IPs not in SAN:${missing}"
    echo "[cert] regenerating leaf cert with all current IPs..."

    if command -v mkcert >/dev/null 2>&1 && regen_via_mkcert "$current_ips"; then
        echo "[cert] regenerated via mkcert — CA unchanged"
        echo "[cert] clients that already trust the CA keep working without action"
    elif regen_via_openssl "$current_ips"; then
        echo "[cert] regenerated via openssl — NEW self-signed CA"
        echo "[cert] clients must reinstall the CA via /cert-guide on first reconnect"
    else
        echo "[cert] regen FAILED — leaving existing cert in place" >&2
        exit 1
    fi

    date +%s > "$CERTS_DIR/.last-regen" 2>/dev/null || true
}

main
