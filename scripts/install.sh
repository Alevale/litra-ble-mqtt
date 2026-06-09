#!/usr/bin/env bash
# Install the Litra MQTT bridge: udev rule, Python venv, systemd service.
#
#   sudo ./scripts/install.sh
#
# Run with sudo. The Python venv is created as the *invoking* user (SUDO_USER)
# so it isn't owned by root. Re-run any time to update; it's idempotent.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo $0" >&2
  exit 1
fi

RUN_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"
ENV_FILE="/etc/litra-mqtt.env"
UNIT="/etc/systemd/system/litra-mqtt.service"

echo ">> Repo:    $REPO_DIR"
echo ">> User:    $RUN_USER"

# 1. udev rule -------------------------------------------------------------
echo ">> Installing udev rule"
install -m 0644 "$REPO_DIR/udev/99-litra-ble.rules" /etc/udev/rules.d/99-litra-ble.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw || true

# 2. Python venv (as the invoking user) ------------------------------------
echo ">> Creating/updating Python venv"
if [[ ! -d "$VENV" ]]; then
  sudo -u "$RUN_USER" python3 -m venv "$VENV"
fi
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet "$REPO_DIR"

# 3. Environment file ------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  echo ">> Creating $ENV_FILE (EDIT IT: set LITRA_MQTT_HOST and credentials)"
  install -m 0640 "$REPO_DIR/litra-mqtt.env.example" "$ENV_FILE"
else
  echo ">> $ENV_FILE already exists, leaving it untouched"
fi

# 4. systemd unit ----------------------------------------------------------
echo ">> Installing systemd unit"
cat > "$UNIT" <<EOF
[Unit]
Description=Litra Beam LX -> Home Assistant MQTT bridge
After=network-online.target bluetooth.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
EnvironmentFile=$ENV_FILE
ExecStart=$VENV/bin/python -m litra_ble.bridge
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable litra-mqtt.service

echo
echo "Done. Next:"
echo "  1. Edit $ENV_FILE  (set LITRA_MQTT_HOST + MQTT credentials)"
echo "  2. sudo systemctl start litra-mqtt.service"
echo "  3. journalctl -u litra-mqtt.service -f"
echo
echo "The two lights should appear in Home Assistant under a 'Litra Beam LX' device."
