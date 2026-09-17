"""Minimal Turing UsbMonitor (rev A) protocol.

The panel enumerates as CDC ACM (VID 1a86 PID 5722, serial USB35INCHIPSV2).
Linux does not treat it as a monitor: we push RGB565 rectangles over serial.
"""

from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass
from enum import IntEnum

import serial
from PIL import Image

DEFAULT_PORT = "/dev/serial/by-id/usb-Turing_UsbMonitor_USB35INCHIPSV2-if00"
VID = 0x1A86
PID = 0x5722
SERIAL_ID = "USB35INCHIPSV2"


class Command(IntEnum):
    RESET = 101
    CLEAR = 102
    SCREEN_OFF = 108
    SCREEN_ON = 109
    SET_BRIGHTNESS = 110
    SET_ORIENTATION = 121
    DISPLAY_BITMAP = 197
    HELLO = 69


class Orientation(IntEnum):
    PORTRAIT = 0
    REVERSE_PORTRAIT = 1
    LANDSCAPE = 2
    REVERSE_LANDSCAPE = 3


HELLO_SIZES = {
    bytes([0x01] * 6): (320, 480, "UsbMonitor 3.5\""),
    bytes([0x02] * 6): (480, 800, "UsbMonitor 5\""),
    bytes([0x03] * 6): (600, 1024, "UsbMonitor 7\""),
}


@dataclass
class DeviceInfo:
    port: str
    width: int
    height: int
    label: str
    hello: bytes


def find_port() -> str | None:
    if os.path.exists(DEFAULT_PORT):
        return DEFAULT_PORT
    for path in glob.glob("/dev/serial/by-id/*Turing*") + glob.glob("/dev/serial/by-id/*UsbMonitor*"):
        return path
    if os.path.exists("/dev/ttyACM0"):
        return "/dev/ttyACM0"
    return None


def pack_command(cmd: int, x: int, y: int, ex: int, ey: int) -> bytes:
    buf = bytearray(6)
    buf[0] = x >> 2
    buf[1] = ((x & 3) << 6) + (y >> 4)
    buf[2] = ((y & 15) << 4) + (ex >> 6)
    buf[3] = ((ex & 63) << 2) + (ey >> 8)
    buf[4] = ey & 255
    buf[5] = cmd
    return bytes(buf)


def image_to_rgb565le(image: Image.Image) -> bytes:
    img = image.convert("RGB")
    src = img.tobytes()
    out = bytearray(img.size[0] * img.size[1] * 2)
    j = 0
    for i in range(0, len(src), 3):
        r, g, b = src[i], src[i + 1], src[i + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = v & 0xFF
        out[j + 1] = v >> 8
        j += 2
    return bytes(out)


class TuringLcd:
    def __init__(self, port: str | None = None, baud: int = 115200):
        self.port = port or find_port()
        if not self.port:
            raise FileNotFoundError(
                "No Turing UsbMonitor serial device found (expected USB35INCHIPSV2)"
            )
        self.ser = serial.Serial(
            self.port,
            baud,
            timeout=1,
            write_timeout=5,
            rtscts=True,
        )
        self.width = 320
        self.height = 480
        self.orientation = Orientation.PORTRAIT
        self.info: DeviceInfo | None = None

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    def __enter__(self) -> "TuringLcd":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            n = self.ser.write(view)
            if not n:
                raise serial.SerialTimeoutException("short serial write")
            view = view[n:]

    def send_command(self, cmd: int, x: int = 0, y: int = 0, ex: int = 0, ey: int = 0) -> None:
        self._write(pack_command(cmd, x, y, ex, ey))

    def hello(self) -> DeviceInfo:
        payload = bytes([Command.HELLO] * 6)
        self.ser.reset_input_buffer()
        self._write(payload)
        old_timeout = self.ser.timeout
        self.ser.timeout = 0.2
        try:
            response = self.ser.read(6)
        finally:
            self.ser.timeout = old_timeout
        self.ser.reset_input_buffer()
        size = HELLO_SIZES.get(response)
        if size:
            width, height, label = size
        else:
            width, height, label = 320, 480, "Turing Smart Screen 3.5\""
        self.width = width
        self.height = height
        self.info = DeviceInfo(self.port, width, height, label, response)
        return self.info

    def screen_on(self) -> None:
        self.send_command(Command.SCREEN_ON)

    def screen_off(self) -> None:
        self.send_command(Command.SCREEN_OFF)

    def set_brightness(self, percent: int) -> None:
        percent = max(0, min(100, percent))
        level = int(255 - ((percent / 100) * 255))
        self.send_command(Command.SET_BRIGHTNESS, level, 0, 0, 0)

    def set_orientation(self, orientation: Orientation = Orientation.PORTRAIT) -> None:
        self.orientation = orientation
        width, height = self.current_size()
        buf = bytearray(16)
        buf[0:6] = pack_command(Command.SET_ORIENTATION, 0, 0, 0, 0)
        buf[6] = orientation + 100
        buf[7] = width >> 8
        buf[8] = width & 255
        buf[9] = height >> 8
        buf[10] = height & 255
        self._write(bytes(buf))

    def current_size(self) -> tuple[int, int]:
        if self.orientation in (Orientation.PORTRAIT, Orientation.REVERSE_PORTRAIT):
            return self.width, self.height
        return self.height, self.width

    def display_image(self, image: Image.Image, x: int = 0, y: int = 0) -> None:
        width, height = self.current_size()
        img = image.convert("RGB")
        img_w, img_h = img.size
        if x + img_w > width:
            img_w = width - x
        if y + img_h > height:
            img_h = height - y
        if img_w != img.size[0] or img_h != img.size[1]:
            img = img.crop((0, 0, img_w, img_h))
        x1 = x + img_w - 1
        y1 = y + img_h - 1
        payload = image_to_rgb565le(img)
        self.send_command(Command.DISPLAY_BITMAP, x, y, x1, y1)
        self._write(payload)
        self.ser.flush()
