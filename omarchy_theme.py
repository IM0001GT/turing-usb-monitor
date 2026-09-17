"""Read the active Omarchy theme into a panel palette.

Uses ~/.local/state/omarchy/current/theme/colors.toml — the copy
omarchy-theme-set installs, including user overlays. Falls back to
Event Horizon if Omarchy is not present.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

STATE = Path.home() / ".local/state/omarchy/current"
COLORS_TOML = STATE / "theme" / "colors.toml"
THEME_NAME = STATE / "theme.name"

# Event Horizon — used when Omarchy state is missing or unreadable.
_FALLBACK = {
    "name": "Event Horizon",
    "bg": (28, 30, 38),
    "card": (35, 37, 48),
    "fg": (203, 206, 208),
    "muted": (111, 111, 112),
    "accent": (38, 187, 217),
    "green": (41, 211, 152),
    "yellow": (250, 194, 154),
    "red": (233, 86, 120),
    "track": (46, 48, 62),
}


def _hex_rgb(value: object, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if not isinstance(value, str):
        return default
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) < 6:
        return default
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return default


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _pick(data: dict, keys: tuple[str, ...], default: tuple[int, int, int]) -> tuple[int, int, int]:
    for key in keys:
        if key in data:
            return _hex_rgb(data[key], default)
    return default


def _pretty_name(slug: str) -> str:
    slug = slug.strip()
    if not slug:
        return "Omarchy"
    return slug.replace("-", " ").replace("_", " ").title()


def _stamp() -> tuple:
    parts: list = []
    for path in (THEME_NAME, COLORS_TOML):
        try:
            st = path.stat()
            parts.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((str(path), 0, 0))
    return tuple(parts)


def _read_name() -> str:
    try:
        return _pretty_name(THEME_NAME.read_text(encoding="utf-8"))
    except OSError:
        return _FALLBACK["name"]


@dataclass(frozen=True)
class Palette:
    name: str
    bg: tuple[int, int, int]
    card: tuple[int, int, int]
    fg: tuple[int, int, int]
    muted: tuple[int, int, int]
    accent: tuple[int, int, int]
    green: tuple[int, int, int]
    yellow: tuple[int, int, int]
    red: tuple[int, int, int]
    track: tuple[int, int, int]
    stamp: tuple

    def load_pct(self, value: int | None) -> tuple[int, int, int]:
        if value is None:
            return self.accent
        if value >= 90:
            return self.red
        if value >= 70:
            return self.yellow
        return self.green

    def changed(self) -> bool:
        return _stamp() != self.stamp


def palette_from_mapping(data: dict, name: str, stamp: tuple) -> Palette:
    bg = _pick(data, ("background", "bg", "color0"), _FALLBACK["bg"])
    fg = _pick(data, ("foreground", "fg", "color7", "color15"), _FALLBACK["fg"])
    muted = _pick(data, ("muted", "dark_foreground", "dark_fg", "color8"), _FALLBACK["muted"])
    accent = _pick(data, ("accent", "blue", "color4", "cyan"), _FALLBACK["accent"])
    green = _pick(data, ("green", "color2", "bright_green"), _FALLBACK["green"])
    yellow = _pick(data, ("yellow", "orange", "color3"), _FALLBACK["yellow"])
    red = _pick(data, ("red", "color1", "bright_red"), _FALLBACK["red"])
    card = _pick(data, ("lighter_background", "lighter_bg", "selection"), bg)
    if card == bg:
        card = _mix(bg, fg, 0.10)
    track = _pick(data, ("selection",), card)
    if track == card or track == bg:
        track = _mix(bg, fg, 0.18)
    return Palette(
        name=name,
        bg=bg,
        card=card,
        fg=fg,
        muted=muted,
        accent=accent,
        green=green,
        yellow=yellow,
        red=red,
        track=track,
        stamp=stamp,
    )


def load_palette() -> Palette:
    stamp = _stamp()
    name = _read_name()
    try:
        with COLORS_TOML.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return Palette(stamp=stamp, **{k: v for k, v in _FALLBACK.items()})
    if not isinstance(data, dict):
        return Palette(stamp=stamp, **{k: v for k, v in _FALLBACK.items()})
    return palette_from_mapping(data, name, stamp)
