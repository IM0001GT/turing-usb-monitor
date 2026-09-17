#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
RULE_SRC="$ROOT/udev/99-turing-usbmonitor.rules"
RULE_DST="/etc/udev/rules.d/99-turing-usbmonitor.rules"
if [[ -n "${PKEXEC_UID:-}" ]]; then
  USER_NAME="$(id -nu "$PKEXEC_UID")"
elif [[ -n "${SUDO_USER:-}" ]]; then
  USER_NAME="$SUDO_USER"
else
  USER_NAME="$USER"
fi

install -m 644 "$RULE_SRC" "$RULE_DST"
udevadm control --reload-rules
if [[ -e /dev/ttyACM0 ]]; then
  udevadm trigger --action=add /sys/class/tty/ttyACM0 || true
  setfacl -m "u:${USER_NAME}:rw" /dev/ttyACM0 || chmod 0660 /dev/ttyACM0
fi
if ! id -nG "$USER_NAME" | grep -qw uucp; then
  usermod -aG uucp "$USER_NAME"
  echo "Added $USER_NAME to group uucp (new sessions pick this up)."
fi
echo "Installed $RULE_DST"
ls -l /dev/ttyACM0 /dev/turing-usbmonitor 2>/dev/null || true
getfacl -p /dev/ttyACM0 2>/dev/null || true
