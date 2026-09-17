#!/usr/bin/env python3
"""Drive the Turing USB IPS panel with a hardware-stats overlay."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import serial
from PIL import Image, ImageDraw, ImageFont

from omarchy_theme import Palette, load_palette
from turing_lcd import Orientation, TuringLcd, find_port

HERE = Path(__file__).resolve().parent
SYSTEM_USAGE = Path.home() / ".config/omarchy/plugins/im0001gt.hw-tooltip/scripts/system-usage"
FONT_REG = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
FONT_MED = "/usr/share/fonts/noto/NotoSans-Medium.ttf"

# Native panel is 320x480 portrait. Compose a 480x320 landscape UI, then
# rotate 90° CCW onto the panel (ROTATE_270 was 180° off: readable but upside down).
LAYOUT_SIZE = (480, 320)
MOUNT_ROTATE = Image.Transpose.ROTATE_90
# Give USB a few minutes after login, then stop. Missing/hung hardware
# must not restart the user service forever.
DEVICE_WAIT_SECS = 180
DEVICE_RETRY_SECS = 2
OPEN_ERRORS = (
    FileNotFoundError,
    PermissionError,
    OSError,
    serial.SerialException,
    serial.SerialTimeoutException,
)




def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def parse_usage(text: str) -> dict:
    info: dict = {"cores": [], "disks": []}
    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        key = parts[0]
        if key == "cpu" and len(parts) >= 2:
            info["cpu"] = int(parts[1])
        elif key == "core" and len(parts) >= 3:
            info["cores"].append(int(parts[2]))
        elif key == "cpu_name":
            info["cpu_name"] = " ".join(parts[1:])
        elif key == "ram" and len(parts) >= 4:
            info["ram_used"] = float(parts[1])
            info["ram_total"] = float(parts[2])
            info["ram_pct"] = int(parts[3])
        elif key == "ram_info":
            info["ram_info"] = " ".join(parts[1:])
        elif key == "gpu" and len(parts) >= 2:
            info["gpu"] = None if parts[1] == "n/a" else int(parts[1])
        elif key == "gpu_name":
            info["gpu_name"] = " ".join(parts[1:])
        elif key == "cpu_temp" and len(parts) >= 2:
            info["cpu_temp"] = None if parts[1] == "n/a" else int(float(parts[1]))
        elif key == "gpu_temp" and len(parts) >= 2:
            info["gpu_temp"] = None if parts[1] == "n/a" else int(float(parts[1]))
        elif key == "ram_temp" and len(parts) >= 2:
            info["ram_temp"] = None if parts[1] == "n/a" else int(float(parts[1]))
        elif key == "disk" and len(parts) >= 5:
            info["disks"].append(
                {
                    "mount": parts[1],
                    "used": float(parts[2]),
                    "total": float(parts[3]),
                    "pct": int(parts[4]),
                    "model": " ".join(parts[5:]),
                }
            )
        elif key == "disk_temp" and len(parts) >= 3:
            info.setdefault("disk_temps", {})[parts[1]] = int(float(parts[2]))
    return info


def sample_usage() -> dict:
    if not SYSTEM_USAGE.exists():
        return {}
    try:
        out = subprocess.check_output([str(SYSTEM_USAGE)], text=True, timeout=8)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return {}
    return parse_usage(out)


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def draw_bar(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    pct: int | None,
    color,
    pal: Palette,
) -> None:
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=pal.track)
    if pct is None:
        return
    fill = max(h, int((w * max(0, min(100, pct))) / 100))
    d.rounded_rectangle([x, y, x + fill, y + h], radius=h // 2, fill=color)


def pick_disk(info: dict) -> dict | None:
    disks = info.get("disks") or []
    for disk in disks:
        if disk["mount"] == "/":
            return disk
    return disks[0] if disks else None


def to_native(frame: Image.Image) -> Image.Image:
    """Map landscape UI onto the native 320x480 panel for this mount."""
    if frame.size != LAYOUT_SIZE:
        frame = frame.resize(LAYOUT_SIZE, Image.Resampling.LANCZOS)
    return frame.transpose(MOUNT_ROTATE)


def draw_card(
    d: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    kind: str,
    name: str,
    value: str,
    temp: str,
    pct: int | None,
    pal: Palette,
) -> None:
    x0, y0, x1, y1 = box
    d.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=pal.card)
    inner = 14
    label_f = font(FONT_MED, 11)
    name_f = font(FONT_REG, 12)
    value_f = font(FONT_MED, 28)
    temp_f = font(FONT_MED, 13)
    max_chars = max(8, (x1 - x0 - inner * 2) // 7)
    color = pal.load_pct(pct)

    # Keep the numbers, name, and bar as one cluster so the card does not look empty.
    block_h = 92
    top = y0 + max(10, (y1 - y0 - block_h) // 2)
    d.text((x0 + inner, top), kind, font=label_f, fill=pal.muted)
    if temp:
        tw = d.textlength(temp, font=temp_f)
        d.text((x1 - inner - tw, top - 1), temp, font=temp_f, fill=color)
    d.text((x0 + inner, top + 16), shorten(name, max_chars), font=name_f, fill=pal.fg)
    d.text((x0 + inner, top + 36), value, font=value_f, fill=pal.fg)
    draw_bar(d, x0 + inner, top + 76, x1 - x0 - inner * 2, 7, pct, color, pal)


def render_panel(width: int, height: int, info: dict, pal: Palette) -> Image.Image:
    img = Image.new("RGB", (width, height), pal.bg)
    d = ImageDraw.Draw(img)
    title_f = font(FONT_MED, 18)
    small_f = font(FONT_REG, 11)
    pad = 10
    gap = 8
    header_h = 32
    now = datetime.now()
    hour = now.hour % 12 or 12
    clock = f"{hour}:{now:%M:%S} {now:%p}"

    d.text((pad, 7), clock, font=title_f, fill=pal.fg)
    clock_w = d.textlength(clock, font=title_f)
    d.text((pad + clock_w + 8, 12), "STATS", font=small_f, fill=pal.accent)
    sub = shorten(pal.name, 28)
    sw = d.textlength(sub, font=small_f)
    d.text((width - pad - sw, 12), sub, font=small_f, fill=pal.muted)

    grid_top = header_h
    grid_h = height - grid_top - pad
    grid_w = width - 2 * pad
    card_w = (grid_w - gap) // 2
    card_h = (grid_h - gap) // 2
    cells = [
        (pad, grid_top, pad + card_w, grid_top + card_h),
        (pad + card_w + gap, grid_top, pad + 2 * card_w + gap, grid_top + card_h),
        (pad, grid_top + card_h + gap, pad + card_w, grid_top + 2 * card_h + gap),
        (pad + card_w + gap, grid_top + card_h + gap, pad + 2 * card_w + gap, grid_top + 2 * card_h + gap),
    ]

    cpu = info.get("cpu")
    cpu_temp = info.get("cpu_temp")
    draw_card(
        d,
        cells[0],
        "CPU",
        info.get("cpu_name") or "CPU",
        f"{cpu if cpu is not None else '—'}%",
        f"{cpu_temp}°" if cpu_temp is not None else "",
        cpu,
        pal,
    )

    gpu = info.get("gpu")
    gpu_temp = info.get("gpu_temp")
    draw_card(
        d,
        cells[1],
        "GPU",
        info.get("gpu_name") or "GPU",
        f"{gpu if gpu is not None else '—'}%",
        f"{gpu_temp}°" if gpu_temp is not None else "",
        gpu,
        pal,
    )

    ram_pct = info.get("ram_pct")
    ram_used = info.get("ram_used")
    ram_total = info.get("ram_total")
    ram_temp = info.get("ram_temp")
    if ram_used is not None and ram_total is not None:
        ram_value = f"{ram_used:.0f}/{ram_total:.0f}G"
    else:
        ram_value = "—"
    draw_card(
        d,
        cells[2],
        "MEMORY",
        info.get("ram_info") or "System RAM",
        ram_value,
        f"{ram_temp}°" if ram_temp is not None else "",
        ram_pct,
        pal,
    )

    disk = pick_disk(info)
    if disk:
        temps = info.get("disk_temps") or {}
        disk_temp = temps.get(disk["mount"])
        draw_card(
            d,
            cells[3],
            "DISK",
            disk.get("model") or disk["mount"],
            f"{disk['used']:.0f}/{disk['total']:.0f}G",
            f"{disk_temp}°" if disk_temp is not None else "",
            disk["pct"],
            pal,
        )
    else:
        draw_card(d, cells[3], "DISK", "—", "—", "", None, pal)

    return img


def dirty_patches(prev: Image.Image, curr: Image.Image, gap: int = 6):
    """Yield (x, y, crop) for changed row-clusters so we skip full-frame USB writes."""
    if prev.size != curr.size:
        yield 0, 0, curr
        return
    w, h = curr.size
    prev_b = prev.tobytes()
    curr_b = curr.tobytes()
    if prev_b == curr_b:
        return
    stride = w * 3
    clusters: list[tuple[int, int]] = []
    start = last = None
    for y in range(h):
        off = y * stride
        dirty = prev_b[off : off + stride] != curr_b[off : off + stride]
        if dirty:
            if start is None:
                start = last = y
            elif y - last <= gap:
                last = y
            else:
                clusters.append((start, last))
                start = last = y
        elif start is not None and y - last > gap:
            clusters.append((start, last))
            start = last = None
    if start is not None:
        clusters.append((start, last))

    for y0, y1 in clusters:
        x0, x1 = w, -1
        for y in range(y0, y1 + 1):
            off = y * stride
            row_p = prev_b[off : off + stride]
            row_c = curr_b[off : off + stride]
            if row_p == row_c:
                continue
            for x in range(w):
                i = x * 3
                if row_p[i : i + 3] != row_c[i : i + 3]:
                    if x < x0:
                        x0 = x
                    if x > x1:
                        x1 = x
        if x1 < 0:
            continue
        x0 = max(0, x0 - 2)
        x1 = min(w - 1, x1 + 2)
        y0 = max(0, y0 - 1)
        y1 = min(h - 1, y1 + 1)
        yield x0, y0, curr.crop((x0, y0, x1 + 1, y1 + 1))


def render_test(width: int, height: int, info_label: str, hello: bytes, pal: Palette) -> Image.Image:
    img = Image.new("RGB", (width, height), pal.bg)
    d = ImageDraw.Draw(img)
    title_f = font(FONT_MED, 22)
    body_f = font(FONT_REG, 13)
    small_f = font(FONT_REG, 12)
    d.rectangle([0, 0, width, 10], fill=pal.accent)
    d.text((16, 18), "TOP", font=small_f, fill=pal.accent)
    d.text((16, 40), "Turing UsbMonitor", font=title_f, fill=pal.fg)
    lines = [
        pal.name,
        info_label,
        f"layout {width} × {height}  landscape",
        "native 320 × 480, rotated 90° CCW",
        f"HELLO {hello.hex() or 'timeout → 3.5\"'}",
    ]
    y = 84
    for line in lines:
        d.text((16, y), line, font=body_f, fill=pal.fg)
        y += 20
    d.text((16, height - 28), "If TOP is not at the physical top, say so.", font=small_f, fill=pal.muted)
    return img


def open_lcd(port: str | None, brightness: int) -> TuringLcd:
    lcd = TuringLcd(port)
    info = lcd.hello()
    lcd.screen_on()
    lcd.set_brightness(brightness)
    lcd.set_orientation(Orientation.PORTRAIT)
    print(f"{info.label}  {info.width}x{info.height}  {info.port}")
    print(f"HELLO {info.hello.hex() or '(no reply, treating as official 3.5)'}")
    return lcd


def wait_for_lcd(
    port: str | None,
    brightness: int,
    timeout: float,
    should_stop=lambda: False,
) -> TuringLcd | None:
    """Open the panel, retrying until timeout. Returns None instead of looping forever."""
    deadline = time.monotonic() + max(0.0, timeout)
    last_log = 0.0
    attempt = 0
    while not should_stop():
        attempt += 1
        try:
            return open_lcd(port, brightness)
        except OPEN_ERRORS as exc:
            left = deadline - time.monotonic()
            now = time.monotonic()
            if attempt == 1 or now - last_log >= 30:
                print(f"UsbMonitor not ready ({exc}); {max(0.0, left):.0f}s left", flush=True)
                last_log = now
            if left <= 0:
                print(
                    "UsbMonitor wait timed out; stopping so a missing panel "
                    "cannot restart forever. Start again with: "
                    "systemctl --user start turing-panel.service",
                    flush=True,
                )
                return None
            time.sleep(min(DEVICE_RETRY_SECS, max(0.2, left)))
    return None


def close_lcd(lcd: TuringLcd | None) -> None:
    if lcd is None:
        return
    try:
        lcd.close()
    except OPEN_ERRORS:
        pass


def cmd_test(args) -> int:
    with open_lcd(args.port, args.brightness) as lcd:
        w, h = LAYOUT_SIZE
        label = lcd.info.label if lcd.info else "Turing"
        hello = lcd.info.hello if lcd.info else b""
        t0 = time.perf_counter()
        lcd.display_image(to_native(render_test(w, h, label, hello, load_palette())))
        print(f"test frame pushed in {time.perf_counter() - t0:.2f}s  layout {w}x{h}")
    return 0


def cmd_run(args) -> int:
    stop = False
    reload_theme = False

    def handle(_sig, _frame):
        nonlocal stop
        stop = True

    def handle_hup(_sig, _frame):
        nonlocal reload_theme
        reload_theme = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGHUP, handle_hup)

    print(f"waiting up to {args.wait_device:.0f}s for UsbMonitor", flush=True)
    lcd = wait_for_lcd(
        args.port, args.brightness, args.wait_device, should_stop=lambda: stop
    )
    if lcd is None:
        return 0

    w, h = LAYOUT_SIZE
    pal = load_palette()
    print(f"theme {pal.name}", flush=True)
    try:
        label = lcd.info.label if lcd.info else "Turing panel"
        prev = None
        while not stop:
            if reload_theme or pal.changed():
                reload_theme = False
                pal = load_palette()
                prev = None
                print(f"theme {pal.name}", flush=True)
            t0 = time.perf_counter()
            usage = sample_usage()
            frame = to_native(render_panel(w, h, usage, pal))
            try:
                if prev is None:
                    lcd.display_image(frame)
                    n_patches = 1
                else:
                    n_patches = 0
                    for x, y, patch in dirty_patches(prev, frame):
                        lcd.display_image(patch, x, y)
                        n_patches += 1
            except OPEN_ERRORS as exc:
                print(f"lost UsbMonitor ({exc}); reconnecting", flush=True)
                close_lcd(lcd)
                lcd = wait_for_lcd(
                    args.port,
                    args.brightness,
                    args.wait_device,
                    should_stop=lambda: stop,
                )
                if lcd is None:
                    return 0
                label = lcd.info.label if lcd.info else label
                prev = None
                continue
            prev = frame
            dt = time.perf_counter() - t0
            print(f"update {n_patches} rects in {dt:.2f}s", flush=True)
            time.sleep(max(0.15, args.interval - dt))
        if args.off_on_exit:
            try:
                lcd.screen_off()
            except OPEN_ERRORS:
                pass
    finally:
        close_lcd(lcd)
    return 0


def cmd_off(args) -> int:
    with TuringLcd(args.port) as lcd:
        lcd.hello()
        lcd.screen_off()
        print("screen off")
    return 0


def cmd_on(args) -> int:
    with TuringLcd(args.port) as lcd:
        lcd.hello()
        lcd.screen_on()
        lcd.set_brightness(args.brightness)
        print("screen on")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Turing USB IPS panel (AIDA64-style gadget)")
    parser.add_argument("--port", default=None, help="serial device (auto-detect by default)")
    parser.add_argument("--brightness", type=int, default=45)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_test = sub.add_parser("test", help="identify the panel and push a test frame")
    p_test.set_defaults(func=cmd_test)

    p_run = sub.add_parser("run", help="live CPU/GPU/RAM/disk overlay")
    p_run.add_argument("--interval", type=float, default=2.0)
    p_run.add_argument("--off-on-exit", action="store_true")
    p_run.add_argument(
        "--wait-device",
        type=float,
        default=DEVICE_WAIT_SECS,
        help="seconds to wait for the USB panel before giving up (default 180)",
    )
    p_run.set_defaults(func=cmd_run)

    p_off = sub.add_parser("off", help="blank the panel")
    p_off.set_defaults(func=cmd_off)

    p_on = sub.add_parser("on", help="turn the panel backlight on")
    p_on.set_defaults(func=cmd_on)

    args = parser.parse_args()
    if args.cmd != "run" and args.port is None and not find_port():
        print("No Turing UsbMonitor found on USB.", file=sys.stderr)
        return 1
    try:
        return args.func(args)
    except PermissionError:
        print(
            "Permission denied on the serial device.\n"
            "Install udev/99-turing-usbmonitor.rules and replug, or:\n"
            "  pkexec setfacl -m u:$USER:rw /dev/ttyACM0",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
