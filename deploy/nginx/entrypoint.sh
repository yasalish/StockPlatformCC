#!/bin/sh
# entrypoint.sh — make sure nginx has a certificate before it starts.
#
# nginx refuses to start at all if ssl_certificate points at a missing file, so
# a first `docker compose up` on a fresh host would fail with a confusing error
# rather than a working (if untrusted) site. This generates a self-signed pair
# on the tls volume when none is present. Replacing those two files with a real
# certificate and reloading is the entire upgrade path — no config change.
set -eu

TLS_DIR=/etc/nginx/tls
CERT="$TLS_DIR/fullchain.pem"
KEY="$TLS_DIR/privkey.pem"
CN="${TLS_SELF_SIGNED_CN:-localhost}"

if [ ! -s "$CERT" ] || [ ! -s "$KEY" ]; then
    echo "[nginx] no certificate in $TLS_DIR — generating a self-signed one for '$CN'."
    echo "[nginx] REPLACE IT before going live: browsers will warn on every visit,"
    echo "[nginx] and the HSTS header in snippets/security-headers.conf must stay"
    echo "[nginx] commented out until a trusted certificate is in place."
    mkdir -p "$TLS_DIR"
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -subj "/CN=$CN" \
        -addext "subjectAltName=DNS:$CN,DNS:localhost,IP:127.0.0.1" \
        -keyout "$KEY" -out "$CERT" 2>/dev/null
    chmod 600 "$KEY"
    chmod 644 "$CERT"
fi

# Validate before handing over: a bad config should fail here with a readable
# message rather than half-starting.
nginx -t

exec "$@"
