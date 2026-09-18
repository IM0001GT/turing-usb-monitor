# Turing USB IPS panel

Linux driver and stats overlay for a **Turing UsbMonitor** / AIDA64-style USB
IPS panel. This is a CDC serial gadget, not a DRM monitor — Hyprland cannot
place windows on it.

| | |
|---|---|
| USB | `1a86:5722` QinHeng, manufacturer Turing |
| Serial | `USB35INCHIPSV2` |
| Node | `/dev/ttyACM0` (`/dev/turing-usbmonitor` after udev) |
| Panel | Official 3.5" 320×480 rev A (HELLO times out) |
| Mount | Native-left is physical top; UI is 480×320 landscape, rotated 90° CCW |

The Windows app (`UsbMonitor.exe`) pushes JPEG/GIF frames over USB serial.
This repo does the same with RGB565 rectangles and a live CPU / GPU / RAM /
disk overlay (12-hour clock). Stats are sampled from the Omarchy Hardware
Tooltip `system-usage` script when that plugin is installed.

Colors follow the **active Omarchy theme**:
`~/.local/state/omarchy/current/theme/colors.toml`. A `theme-set` hook sends
SIGHUP so a swap is immediate; the daemon also polls that file so a missed
hook still picks up the next frame.

A full frame takes about 2.4s on this USB link. Live mode sends changed
full-width scanlines (narrow rectangles scramble glyphs on rev A) and
repaints the whole panel every ~30s.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
pkexec "$PWD/install-udev.sh"
ln -sf "$PWD/turing-panel" ~/.local/bin/turing-panel
install -m 644 systemd/turing-panel.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now turing-panel.service
omarchy hook install theme-set "$PWD/hooks/turing-panel-theme"
```

You must be in group `uucp` (the udev script adds you; new logins pick it up).

## Commands

```bash
turing-panel test    # identity + test pattern
turing-panel run     # live overlay
turing-panel off     # backlight off
turing-panel on
```

Stop the user service with `systemctl --user stop turing-panel.service`.
Ctrl+C on a manual `run` keeps the last frame on the panel.

If the panel is unplugged or hung at login, `run` retries for **3 minutes**
then exits 0 so systemd does not keep restarting. Replug and
`systemctl --user start turing-panel.service` (or log in again) to try once
more. Unexpected crashes still restart, but only five times in three minutes.
