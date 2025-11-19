#!/bin/bash
set -e

# --- This section runs as ROOT ---

CERT_FILE="/tmp/mitm-certs/mitmproxy-ca-cert.pem"
TRUST_STORE_DIR="/usr/local/share/ca-certificates"
WAIT_TIMEOUT=30
WAIT_COUNTER=0

# 1. Wait for the certificate
echo "[Entrypoint] Running as root. Waiting for mitmproxy CA certificate..."
while [ ! -f "$CERT_FILE" ]; do
  if [ $WAIT_COUNTER -ge $WAIT_TIMEOUT ]; then
    echo "[Entrypoint] ERROR: Timed out after ${WAIT_TIMEOUT}s waiting for mitmproxy certificate." >&2
    exit 1
  fi
  sleep 1
  WAIT_COUNTER=$((WAIT_COUNTER + 1))
done
echo "[Entrypoint] Certificate found."

# 2. Install certificate
echo "[Entrypoint] Installing certificate..."
cp "$CERT_FILE" "${TRUST_STORE_DIR}/mitmproxy-ca-cert.crt"
update-ca-certificates

# 3. FIX PERMISSIONS (Critical Step)
# We must ensure 'coder' owns the home directory and the mounted volumes.
# This fixes the EACCES error.
echo "[Entrypoint] Fixing permissions for /home/coder..."

find /home/coder \
  -path /home/coder/.config -prune \
  -o -exec chown coder:coder {} +

# 4. Drop privileges and execute
echo "[Entrypoint] Dropping root privileges and starting application as 'coder'..."
cd /home/coder/project
exec gosu coder "$@"