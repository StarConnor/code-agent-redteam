#!/bin/bash
set -e

# --- This section runs as ROOT ---

CERT_FILE="/tmp/mitm-certs/mitmproxy-ca-cert.pem"
TRUST_STORE_DIR="/usr/local/share/ca-certificates"
WAIT_TIMEOUT=30
WAIT_COUNTER=0

# 1. Wait for the certificate, with a timeout
echo "[Entrypoint] Running as root. Waiting for mitmproxy CA certificate..."
while [ ! -f "$CERT_FILE" ]; do
  if [ $WAIT_COUNTER -ge $WAIT_TIMEOUT ]; then
    echo "[Entrypoint] ERROR: Timed out after ${WAIT_TIMEOUT}s waiting for mitmproxy certificate." >&2
    exit 1
  fi
  sleep 1
  WAIT_COUNTER=$((WAIT_COUNTER + 1))
done
echo "[Entrypoint] Certificate found after ${WAIT_COUNTER}s."

# 2. Copy the certificate and update the system trust store (requires root)
echo "[Entrypoint] Installing certificate..."
cp "$CERT_FILE" "${TRUST_STORE_DIR}/mitmproxy-ca-cert.crt"
update-ca-certificates
echo "[Entrypoint] CA certificates updated successfully."

# --- This is the final and most important step ---
#
# 3. Drop privileges and execute the main command (`CMD`) as the 'coder' user.
# The 'exec' command replaces the script process with the code-server process.
# '$@' passes all the arguments from the Dockerfile's CMD instruction.
echo "[Entrypoint] Dropping root privileges and starting application as 'coder'..."
cd /home/coder/project
exec gosu coder "$@"