#!/usr/bin/env bash
# Deploys systemd units for the Record Player setup.
# By default: web + pipe + owntone override.
# Optionally: HomeKit (with --with-homekit).

set -euo pipefail

# -----------------------------
# Parse CLI flags
# -----------------------------
WITH_HOMEKIT=0

for arg in "$@"; do
  case "$arg" in
    --with-homekit)
      WITH_HOMEKIT=1
      ;;
    --help|-h)
      cat <<EOF
Usage: $(basename "$0") [--with-homekit]

Deploy systemd unit files for the Record Player stack.

  (no args)        Deploy:
                     - record-player-web.service
                     - owntone-record_player-input.service
                     - owntone.service.d/override.conf
                     and restart record-player-web.service.

  --with-homekit   Additionally deploy and restart:
                     - record-player-homekit.service

EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Try: $(basename "$0") --help" >&2
      exit 1
      ;;
  esac
done

# Resolve repo root (script/..)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Sources
UNIT_WEB_SRC="$REPO_DIR/systemd/record-player-web.service"
UNIT_PIPE_SRC="$REPO_DIR/systemd/owntone-record_player-input.service"
UNIT_HOMEKIT_SRC="$REPO_DIR/systemd/record-player-homekit.service"
DROPIN_FILE_SRC="$REPO_DIR/systemd/owntone.service.d/override.conf"

# Destinations
SYSTEMD_DIR="/etc/systemd/system"
UNIT_WEB_DST="$SYSTEMD_DIR/record-player-web.service"
UNIT_PIPE_DST="$SYSTEMD_DIR/owntone-record_player-input.service"
UNIT_HOMEKIT_DST="$SYSTEMD_DIR/record-player-homekit.service"
DROPIN_DIR_DST="$SYSTEMD_DIR/owntone.service.d"
DROPIN_FILE_DST="$DROPIN_DIR_DST/override.conf"

# Helper: ensure file exists
require_file() {
  [[ -f "$1" ]] || { echo "ERROR: Missing file: $1" >&2; exit 1; }
}

echo "Deploying systemd unit files from: $REPO_DIR"

# Sanity checks
require_file "$UNIT_WEB_SRC"
require_file "$UNIT_PIPE_SRC"
require_file "$DROPIN_FILE_SRC"

if [[ "$WITH_HOMEKIT" -eq 1 ]]; then
  require_file "$UNIT_HOMEKIT_SRC"
fi

# Copy standalone units
echo "-> Installing record-player-web.service"
sudo install -m 0644 "$UNIT_WEB_SRC" "$UNIT_WEB_DST"

echo "-> Installing owntone-record_player-input.service"
sudo install -m 0644 "$UNIT_PIPE_SRC" "$UNIT_PIPE_DST"

if [[ "$WITH_HOMEKIT" -eq 1 ]]; then
  echo "-> Installing record-player-homekit.service"
  sudo install -m 0644 "$UNIT_HOMEKIT_SRC" "$UNIT_HOMEKIT_DST"
fi

# Copy drop-in override for owntone.service
echo "-> Installing owntone.service override"
sudo install -d "$DROPIN_DIR_DST"
sudo install -m 0644 "$DROPIN_FILE_SRC" "$DROPIN_FILE_DST"

# Reload systemd to pick up changes
echo "-> Reloading systemd daemon"
sudo systemctl daemon-reload

# Enable and restart web service
echo "-> Enabling and restarting record-player-web.service"
sudo systemctl enable record-player-web.service
sudo systemctl restart record-player-web.service

if [[ "$WITH_HOMEKIT" -eq 1 ]]; then
  echo "-> Enabling and restarting record-player-homekit.service"
  sudo systemctl enable record-player-homekit.service
  sudo systemctl restart record-player-homekit.service
fi

# Ensure Owntone services do not autostart on boot
echo "-> Disabling Owntone services for autostart"
sudo systemctl disable owntone.service || true
sudo systemctl disable owntone-record_player-input.service || true

echo "✅ Deployment complete."
