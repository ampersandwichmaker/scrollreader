#!/usr/bin/env python3
"""
ScrollReader — focused PDF reader with line-by-line navigation, AI command family.
Full version with complete LibraryWidget and GamepadManager.
"""

import sys, json, os, re, time, hashlib
from pathlib import Path
from collections import namedtuple
from typing import Optional, Callable

import fitz
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QLineEdit
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import (QPainter, QColor, QFont, QPixmap, QImage,
                          QKeyEvent, QPolygon, QWheelEvent, QMouseEvent,
                          QFontMetrics, QFontDatabase)

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

try:
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

# ---------------------------------------------------------------------------
# Amber phosphor theme
# ---------------------------------------------------------------------------

AMBER         = QColor("#ffbb33")
AMBER_BRIGHT  = QColor("#ffb000")
AMBER_DIM     = QColor("#996600")
AMBER_DARK    = QColor("#553300")
AMBER_VERY_DIM= QColor("#332200")
AMBER_INV_BG  = QColor("#ffbb33")
AMBER_INV_FG  = QColor("#000000")
UI_BG         = QColor("#000000")

DEFAULT_SWATCH = [
    "#ffbb33", "#e69900", "#cc7700", "#b36200",
    "#ff9900", "#ffcc66", "#cc8800", "#aa5500",
    "#ff8800", "#d4a017",
]

_UI_FONT_FAMILY_ref = ["Courier New"]
_UI_FONT_OFFSET_ref = [0]
_pdf_invert_ref = [False]

def _app_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _load_vga_font() -> str:
    base = _app_dir()
    meipass = getattr(sys, '_MEIPASS', base)
    candidates = [
        os.path.join(meipass, "fonts", "Px437_IBM_VGA_8x16.ttf"),
        os.path.join(meipass, "fonts", "Px437_IBM_VGA-8x16.ttf"),
        os.path.join(base,    "fonts", "Px437_IBM_VGA_8x16.ttf"),
        os.path.join(base,    "fonts", "Px437_IBM_VGA-8x16.ttf"),
        os.path.join(base,    "fonts", "Web437_IBM_VGA_8x16.ttf"),
    ]
    for path in candidates:
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                families = QFontDatabase.applicationFontFamilies(fid)
                if families:
                    _UI_FONT_FAMILY_ref[0] = families[0]
                    return families[0]
    return _UI_FONT_FAMILY_ref[0]

def _ui_font(size: int = 10, bold: bool = False) -> QFont:
    f = QFont(_UI_FONT_FAMILY_ref[0], max(6, size + _UI_FONT_OFFSET_ref[0]))
    if bold:
        f.setWeight(QFont.Weight.Bold)
    return f

def _mk_pen(color: QColor, width: int = 1):
    from PyQt6.QtGui import QPen
    p = QPen(color)
    p.setWidth(width)
    return p

def _apply_theme(config):
    global AMBER, AMBER_BRIGHT, AMBER_DIM, AMBER_DARK, AMBER_VERY_DIM
    global AMBER_INV_BG, AMBER_INV_FG, UI_BG
    AMBER          = QColor(config.get("theme_primary")  or "#ffbb33")
    AMBER_BRIGHT   = QColor(config.get("theme_bright")   or "#ffb000")
    AMBER_DIM      = QColor(config.get("theme_dim")      or "#996600")
    AMBER_DARK     = QColor(config.get("theme_dark")     or "#553300")
    AMBER_VERY_DIM = QColor(config.get("theme_very_dim") or "#332200")
    AMBER_INV_BG   = QColor(config.get("theme_inv_bg")   or "#ffbb33")
    AMBER_INV_FG   = QColor(config.get("theme_inv_fg")   or "#000000")
    UI_BG          = QColor(config.get("theme_bg")       or "#000000")

try:
    from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
    HAS_PRINT = True
except ImportError:
    HAS_PRINT = False

import colorsys

# ---------------------------------------------------------------------------
# Colour swatches
# ---------------------------------------------------------------------------

SWATCHES = {
    "amber":      {"bg": "#000000", "primary": "#ffbb33", "bright": "#ffb000",
                   "dim": "#996600", "dark": "#553300", "very_dim": "#332200",
                   "inv_bg": "#ffbb33", "inv_fg": "#000000"},
    "phosphor":   {"bg": "#000000", "primary": "#33ff66", "bright": "#66ff99",
                   "dim": "#1a8c3a", "dark": "#0d4d20", "very_dim": "#061a0a",
                   "inv_bg": "#33ff66", "inv_fg": "#000000"},
    "cyan":       {"bg": "#000000", "primary": "#00ffcc", "bright": "#66ffee",
                   "dim": "#008866", "dark": "#004433", "very_dim": "#001a11",
                   "inv_bg": "#00ffcc", "inv_fg": "#000000"},
    "blood":      {"bg": "#0a0000", "primary": "#ff3333", "bright": "#ff6666",
                   "dim": "#991111", "dark": "#550000", "very_dim": "#220000",
                   "inv_bg": "#ff3333", "inv_fg": "#000000"},
    "ice":        {"bg": "#000811", "primary": "#aaddff", "bright": "#ddeeff",
                   "dim": "#336699", "dark": "#1a3355", "very_dim": "#0a1a2a",
                   "inv_bg": "#aaddff", "inv_fg": "#000811"},
    "paper":      {"bg": "#f5e6c8", "primary": "#3d2800", "bright": "#1a0f00",
                   "dim": "#7a5500", "dark": "#b8a070", "very_dim": "#ddd0b0",
                   "inv_bg": "#3d2800", "inv_fg": "#f5e6c8"},
    "slate":      {"bg": "#0a0f14", "primary": "#c8d8e8", "bright": "#eef4fa",
                   "dim": "#5a7a99", "dark": "#2a3d55", "very_dim": "#141f2a",
                   "inv_bg": "#c8d8e8", "inv_fg": "#0a0f14"},
    "gold":       {"bg": "#0a0800", "primary": "#ffd700", "bright": "#ffec66",
                   "dim": "#997f00", "dark": "#554500", "very_dim": "#221c00",
                   "inv_bg": "#ffd700", "inv_fg": "#000000"},
    "blue_lcd":   {"bg": "#2233cc", "primary": "#ffffff", "bright": "#ffffff",
                   "dim": "#aabbff", "dark": "#6677dd", "very_dim": "#3344aa",
                   "inv_bg": "#ffffff", "inv_fg": "#2233cc"},
    "green_lcd":  {"bg": "#ccff00", "primary": "#111111", "bright": "#000000",
                   "dim": "#445500", "dark": "#aabb00", "very_dim": "#bbdd00",
                   "inv_bg": "#111111", "inv_fg": "#ccff00"},
    "mono_dark":  {"bg": "#000000", "primary": "#ffffff", "bright": "#ffffff",
                   "dim": "#888888", "dark": "#444444", "very_dim": "#222222",
                   "inv_bg": "#ffffff", "inv_fg": "#000000"},
    "mono_light": {"bg": "#ffffff", "primary": "#000000", "bright": "#000000",
                   "dim": "#555555", "dark": "#aaaaaa", "very_dim": "#dddddd",
                   "inv_bg": "#000000", "inv_fg": "#ffffff"},
    "ember":      {"bg": "#ffffff", "primary": "#cc7700", "bright": "#995500",
                   "dim": "#ddaa66", "dark": "#eeccaa", "very_dim": "#f5e8d8",
                   "inv_bg": "#cc7700", "inv_fg": "#ffffff"},
    "rose":       {"bg": "#fff0f5", "primary": "#cc2266", "bright": "#990044",
                   "dim": "#dd88aa", "dark": "#eebbd0", "very_dim": "#f8dde8",
                   "inv_bg": "#cc2266", "inv_fg": "#ffffff"},
    "lcd":        {"bg": "#000000", "primary": "#e9fa72", "bright": "#f5ff99",
                   "dim": "#8a9a20", "dark": "#4a5510", "very_dim": "#252d05",
                   "inv_bg": "#e9fa72", "inv_fg": "#000000"},
}
SWATCH_NAMES = list(SWATCHES.keys())
_current_swatch_ref = [0]

def _apply_swatch(name: str, config):
    s = SWATCHES.get(name, SWATCHES["amber"])
    config.data["theme_primary"]  = s["primary"]
    config.data["theme_bright"]   = s["bright"]
    config.data["theme_dim"]      = s["dim"]
    config.data["theme_dark"]     = s["dark"]
    config.data["theme_very_dim"] = s["very_dim"]
    config.data["theme_inv_bg"]   = s["inv_bg"]
    config.data["theme_inv_fg"]   = s["inv_fg"]
    config.data["theme_bg"]       = s["bg"]
    config.data["current_swatch"] = name
    config.save()
    _apply_theme(config)

def _scan_fonts() -> list:
    seen = {}
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        bundled = os.path.join(meipass, "fonts")
        if os.path.exists(bundled):
            for f in os.listdir(bundled):
                if f.lower().endswith(('.ttf', '.otf', '.otb')):
                    seen[f.lower()] = os.path.join(bundled, f)
    user_dir = os.path.join(_app_dir(), "fonts")
    if os.path.exists(user_dir):
        for f in os.listdir(user_dir):
            if f.lower().endswith(('.ttf', '.otf', '.otb')):
                seen[f.lower()] = os.path.join(user_dir, f)
    return sorted(seen.values())

def _load_font_by_path(path: str) -> str:
    fid = QFontDatabase.addApplicationFont(path)
    if fid >= 0:
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            return families[0]
    return _UI_FONT_FAMILY_ref[0]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOP_BAR_H    = 28
BOTTOM_BAR_H = 28
L_MARGIN     = 0
R_MARGIN     = 0
PANEL_W      = 160
BORDER_W     = 2
APP_DIR      = Path.home() / ".scrollreader"
CONFIG_PATH  = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.json"

DEFAULT_CONFIG = {
    "reopen_last":           True,
    "midpoint":              0.42,
    "zoom_mode":             "fit-width",
    "zoom_fixed":            1.5,
    "indicator_color":       "#ffb000",
    "highlight_alpha":       35,
    "highlight_height":      20,
    "highlight_offset":      0,
    "saved_highlight_color": "#ffb000",
    "saved_highlight_alpha": 30,
    "bookmark_color":        "#ffbb33",
    "note_color":            "#ffbb33",
    "background_color":      "#000000",
    "statusbar_color":       "#000000",
    "statusbar_text_color":  "#ffbb33",
    "page_gap":              30,
    "export_dir":            "",
    "export_mode":           "timestamped",
    "library_dir":           "",
    "library_recursive":     False,
    "library_swatch":        [],
    "read_tab_sizing":       "flat",
    "theme_primary":         "#ffbb33",
    "theme_bright":          "#ffb000",
    "theme_dim":             "#996600",
    "theme_dark":            "#553300",
    "theme_very_dim":        "#332200",
    "theme_inv_bg":          "#ffbb33",
    "theme_inv_fg":          "#000000",
    "theme_bg":              "#000000",
    "margin_side":           "right",
    "ui_border_width":       2,
    "ui_font_offset":        0,
    "current_swatch":        "amber",
    "current_font_idx":      0,
    "preload_inverted":      True,
    "start_fullscreen":      True,
    "top_bar_h":             28,
    "bottom_bar_h":          28,
    "panel_w":               160,
    "reference_dir":         None,
    "cache_window":          12,
    "inv_window":            6,
    "help_col_offset":       0,
    "library_flip_mode":     False,
    "eager_pages":           2,
    "progress_bar_style":    "full",
    "wizard_completed":      False,
    "translate_provider":    "anthropic",
    "translate_api_key":     "",
    "translate_target_lang": "",
    "translate_ollama_host": "http://localhost:11434",
    "translate_ollama_model":"qwen3:8b",
    "ai_provider":           "anthropic",
    "ai_api_key":            "",
    "ai_model":              "claude-haiku-4-5-20251001",
    "ai_context_lines":      5,
}

ZOOM_MODES   = ["fit-width", "fit-page", "50%", "75%", "100%", "110%", "120%"]
EBOOK_EXTS   = {".pdf", ".epub", ".cbz", ".mobi", ".fb2", ".xps"}

OVERRIDABLE_KEYS = {
    "indicator_color", "highlight_alpha", "highlight_height", "highlight_offset",
    "saved_highlight_color", "saved_highlight_alpha", "bookmark_color", "note_color",
}

ALL_ANNOTATION_KINDS = ["bookmarks", "notes", "highlights"]

# ---------------------------------------------------------------------------
# Range spec
# ---------------------------------------------------------------------------

RangeSpec = namedtuple("RangeSpec", ["mode", "start", "end", "fwd", "back", "error"],
                        defaults=[0, 0, 0, 0, ""])

def parse_range_spec(spec: str, hl_style: bool = False) -> RangeSpec:
    if not spec:
        return RangeSpec(mode="current")
    m = re.fullmatch(r"(\d+)-(\d+)", spec)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        if s > e:
            return RangeSpec(mode="error", error=f"invalid range {s}-{e}: start must be ≤ end")
        return RangeSpec(mode="absolute", start=s, end=e)
    m = re.fullmatch(r"(\d*);(\d*)", spec)
    if m:
        fwd  = int(m.group(1)) if m.group(1) else 0
        back = int(m.group(2)) if m.group(2) else 0
        return RangeSpec(mode="relative", fwd=fwd, back=back)
    m = re.fullmatch(r"(\d+)", spec)
    if m:
        n = int(m.group(1))
        if hl_style:
            return RangeSpec(mode="relative", fwd=n, back=0)
        else:
            return RangeSpec(mode="absolute", start=n, end=n)
    return RangeSpec(mode="error", error=f"unknown range: '{spec}'")

def _extract_note(text: str) -> tuple[str, Optional[str]]:
    m = re.search(r";;(.*);;$", text.strip())
    if m:
        return text[:m.start()].strip(), m.group(1)
    return text.strip(), None

# ---------------------------------------------------------------------------
# Shortcut parser
# ---------------------------------------------------------------------------

RANGE_CHARS = r"[0-9;-]*"

def parse_shortcut(text: str) -> Optional[dict]:
    raw  = text.strip()
    base, note = _extract_note(raw)
    b = base.lower()

    if b == "removeall+":
        return {"cmd": "removeall_plus"}
    if b == "removeall":
        return {"cmd": "removeall", "reason": note}
    if b == "e":
        return {"cmd": "export_all"}

    for pat, cmd in [
        (r"(gl|gotoline)(\d+)",      "goto_line"),
        (r"(gp|gotopage)(\d+)",      "goto_page"),
        (r"(lb|lineback)(\d+)?",     "line_back"),
        (r"(lf|lineforward)(\d+)?",  "line_forward"),
        (r"(pb|pageback)(\d+)?",     "page_back"),
        (r"(pf|pageforward)(\d+)?",  "page_forward"),
    ]:
        m = re.fullmatch(pat, b)
        if m:
            val = int(m.group(2)) if m.group(2) else 1
            key = "line" if cmd in ("goto_line",) else "page" if cmd == "goto_page" else "count"
            return {"cmd": cmd, key: val}

    for pat, cmd, hl in [
        (r"(nl|noteline)(" + RANGE_CHARS + r")",         "note",           False),
        (r"(an|audionote)(" + RANGE_CHARS + r")",        "audio_note",     False),
        (r"(bl|bookmarkline)(" + RANGE_CHARS + r")",     "bookmark_line",  False),
        (r"(bp|bookmarkpage)(" + RANGE_CHARS + r")",     "bookmark_page",  False),
        (r"(hl|highlightline)(" + RANGE_CHARS + r")",    "highlight_line", False),
        (r"(hp|highlightpage)(" + RANGE_CHARS + r")",    "highlight_page", False),
    ]:
        m = re.fullmatch(pat, b)
        if m:
            rs = parse_range_spec(m.group(2), hl_style=hl)
            return {"cmd": cmd, "range": rs, "note": note or ""}

    for pat, cmd in [
        (r"(rl|removeline)(" + RANGE_CHARS + r")",      "remove_line"),
        (r"(rp|removepage)(" + RANGE_CHARS + r")",      "remove_page"),
        (r"(rb|removebookmark)(" + RANGE_CHARS + r")",  "remove_bookmark"),
        (r"(rn|removenote)(" + RANGE_CHARS + r")",      "remove_note"),
        (r"(ran|removeaudionote)(" + RANGE_CHARS + r")","remove_audio_note"),
        (r"(rh|removehighlight)(" + RANGE_CHARS + r")", "remove_highlight"),
    ]:
        m = re.fullmatch(pat, b)
        if m:
            rs = parse_range_spec(m.group(2), hl_style=False)
            return {"cmd": cmd, "range": rs, "reason": note}

    for pat, cmd in [
        (r"(el|exportline)(" + RANGE_CHARS + r")",      "export_line"),
        (r"(ep|exportpage)(" + RANGE_CHARS + r")",      "export_page"),
        (r"(eb|exportbookmark)(" + RANGE_CHARS + r")",  "export_bookmark"),
        (r"(en|exportnote)(" + RANGE_CHARS + r")",      "export_note"),
        (r"(eh|exporthighlight)(" + RANGE_CHARS + r")", "export_highlight"),
    ]:
        m = re.fullmatch(pat, b)
        if m:
            rs = parse_range_spec(m.group(2), hl_style=False)
            return {"cmd": cmd, "range": rs}

    if b == "pd":
        return {"cmd": "print_dialog"}
    m = re.fullmatch(r"(pp|printpage)(" + RANGE_CHARS + r")", b)
    if m:
        rs = parse_range_spec(m.group(2), hl_style=False)
        return {"cmd": "print_pages", "range": rs}

    return None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class Config:
    def __init__(self):
        self.data = DEFAULT_CONFIG.copy()
        self._load()

    def _load(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.data, f, indent=2)

    def get(self, key, default=None):
        return self.data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set(self, key: str, raw: str) -> str:
        existing = DEFAULT_CONFIG.get(key, self.data.get(key))
        try:
            if isinstance(existing, bool):   value = raw.lower() in ("true","1","yes","on")
            elif isinstance(existing, float): value = float(raw)
            elif isinstance(existing, int):   value = int(raw)
            else:                             value = raw
        except (ValueError, AttributeError): value = raw
        self.data[key] = value
        self.save()
        return f"set {key} = {value}"

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def _book_defaults() -> dict:
    return {
        "line": 0, "timestamp": time.time(),
        "title": None, "author": None,
        "status": "unread", "rating": None, "tags": [],
        "date_started": None, "date_finished": None,
        "total_lines": None, "total_pages": None,
        "bookmarks": [], "notes": [], "highlights": [],
        "removals": [],
        "config_overrides": {},
        "pdf_invert": False,
    }

class History:
    def __init__(self):
        self.data: dict = {}
        self._load()

    def _load(self):
        if HISTORY_PATH.exists():
            try:
                with open(HISTORY_PATH) as f:
                    self.data = json.load(f)
            except Exception:
                pass

    def _save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "w") as f:
            json.dump(self.data, f, indent=2)

    def _entry(self, filepath: str) -> dict:
        key = str(filepath)
        if key not in self.data:
            self.data[key] = _book_defaults()
        else:
            for k, v in _book_defaults().items():
                self.data[key].setdefault(k, v)
        return self.data[key]

    def get_line(self, filepath: str) -> int:
        return self._entry(filepath).get("line", 0)

    def set_line(self, filepath: str, line: int):
        e = self._entry(filepath)
        e["line"] = line
        e["timestamp"] = time.time()
        if e["status"] == "unread" and line > 0:
            e["status"] = "reading"
            e.setdefault("date_started", time.time())
        self._save()

    def set_totals(self, filepath: str, lines: int, pages: int):
        e = self._entry(filepath)
        e["total_lines"] = lines
        e["total_pages"]  = pages
        self._save()

    def set_meta(self, filepath: str, field: str, value: str) -> str:
        allowed = {"title","author","status","rating","tags"}
        if field not in allowed:
            return f"unknown field '{field}'  allowed: {', '.join(sorted(allowed))}"
        e = self._entry(filepath)
        if field == "tags":      e["tags"]   = [t.strip() for t in value.split(",") if t.strip()]
        elif field == "rating":
            try: e["rating"] = int(value)
            except ValueError: return "rating must be an integer"
        else: e[field] = value
        self._save()
        return f"meta {field} = {e[field]}"

    def add_note(self, filepath, line, note) -> str:
        self._entry(filepath)["notes"].append({"line": line, "note": note, "timestamp": time.time()})
        self._save()
        return f"note at line {line+1}" + (f": {note}" if note else "")

    def add_audio_note(self, filepath, line, audio_path) -> str:
        e = self._entry(filepath)
        if "audio_notes" not in e: e["audio_notes"] = []
        e["audio_notes"].append({"line": line, "audio_path": audio_path, "timestamp": time.time()})
        self._save()
        return f"audio note at line {line+1}"

    def add_bookmark(self, filepath, line, page, note) -> str:
        self._entry(filepath)["bookmarks"].append({"line": line, "page": page, "note": note or "", "timestamp": time.time()})
        self._save()
        return f"bookmark line {line+1} (page {page+1})" + (f": {note}" if note else "")

    def add_highlight(self, filepath, start, end, note) -> str:
        self._entry(filepath)["highlights"].append({"start_line": start, "end_line": end, "note": note or "", "timestamp": time.time()})
        self._save()
        return f"highlighted {end-start+1} line(s)" + (f": {note}" if note else "")

    def set_override(self, filepath, key, raw) -> str:
        if key not in OVERRIDABLE_KEYS:
            return f"not overridable: {key}  allowed: {', '.join(sorted(OVERRIDABLE_KEYS))}"
        e = self._entry(filepath)
        existing = DEFAULT_CONFIG.get(key)
        try:
            value = float(raw) if isinstance(existing, float) else (int(raw) if isinstance(existing, int) else raw)
        except (ValueError, TypeError):
            value = raw
        e["config_overrides"][key] = value
        self._save()
        return f"book override {key} = {value}"

    def remove_annotations(self, filepath, start_line, end_line, kinds, reason="") -> tuple:
        e = self._entry(filepath)
        removed, parts = 0, []

        def l_touch(l):       return start_line <= l <= end_line
        def r_touch(sl, el):  return sl <= end_line and el >= start_line

        for kind in kinds:
            before = len(e[kind])
            if kind == "highlights":
                e[kind] = [h for h in e[kind] if not r_touch(h["start_line"], h["end_line"])]
            else:
                e[kind] = [x for x in e[kind] if not l_touch(x["line"])]
            n = before - len(e[kind])
            if n:
                removed += n
                parts.append(f"{n} {kind[:-1]}{'s' if n>1 else ''}")

        if removed:
            e["removals"].append({"timestamp": time.time(), "start_line": start_line,
                                   "end_line": end_line, "kinds": kinds,
                                   "count": removed, "reason": reason or ""})
            self._save()
        return removed, (", ".join(parts) if parts else "nothing")

    def remove_all(self, filepath, reason="") -> int:
        e = self._entry(filepath)
        n = sum(len(e[k]) for k in ALL_ANNOTATION_KINDS)
        for k in ALL_ANNOTATION_KINDS:
            e[k] = []
        if n:
            e["removals"].append({"timestamp": time.time(), "kinds": ALL_ANNOTATION_KINDS,
                                   "count": n, "reason": reason or "", "all": True})
            self._save()
        return n

    def remove_all_plus(self, filepath):
        if str(filepath) in self.data:
            del self.data[str(filepath)]
            self._save()

    def count_in_range(self, filepath, start_line, end_line, kinds) -> int:
        e = self._entry(filepath)
        count = 0
        def l_touch(l):      return start_line <= l <= end_line
        def r_touch(sl, el): return sl <= end_line and el >= start_line
        for kind in kinds:
            if kind == "highlights":
                count += sum(1 for h in e[kind] if r_touch(h["start_line"], h["end_line"]))
            else:
                count += sum(1 for x in e[kind] if l_touch(x["line"]))
        return count

    def collect_in_range(self, filepath, start_line, end_line, kinds) -> dict:
        e = self._entry(filepath)
        result = {}
        def l_touch(l):      return start_line <= l <= end_line
        def r_touch(sl, el): return sl <= end_line and el >= start_line
        if "bookmarks"  in kinds: result["bookmarks"]  = [b for b in e["bookmarks"]  if l_touch(b["line"])]
        if "notes"      in kinds: result["notes"]      = [n for n in e["notes"]      if l_touch(n["line"])]
        if "highlights" in kinds: result["highlights"] = [h for h in e["highlights"] if r_touch(h["start_line"], h["end_line"])]
        return result

    def set_favorite(self, filepath: str, val: bool) -> str:
        self._entry(filepath)["favorite"] = val
        self._save()
        return ("★ added to favorites" if val else "removed from favorites")

    def last_file(self) -> Optional[str]:
        if not self.data:
            return None
        return max(self.data, key=lambda k: self.data[k].get("timestamp", 0))

    def summary(self, filepath) -> str:
        e = self._entry(filepath)
        tl  = e.get("total_lines") or "?"
        cur = e.get("line", 0)
        pct = f"{100*cur/tl:.1f}%" if isinstance(tl, int) and tl > 0 else "?"
        return (f"title: {e.get('title') or 'unknown'}  author: {e.get('author') or 'unknown'}  "
                f"status: {e.get('status')}  rating: {e.get('rating') or '-'}  "
                f"tags: {', '.join(e.get('tags') or []) or 'none'}  |  "
                f"progress: {cur}/{tl} ({pct})  pages: {e.get('total_pages') or '?'}  |  "
                f"bookmarks: {len(e.get('bookmarks',[]))}  notes: {len(e.get('notes',[]))}  "
                f"highlights: {len(e.get('highlights',[]))}")

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _highlight_label(h: dict) -> str:
    sl, el = h["start_line"]+1, h["end_line"]+1
    return f"**Line {sl}**" if sl == el else f"**Lines {sl}–{el}**"

def build_export_md(title: str, items: dict, subtitle: str = "") -> str:
    ts  = time.strftime("%Y-%m-%d %H:%M")
    out = [f"# {title}"]
    if subtitle:
        out.append(f"*{subtitle}*")
    out.append(f"*Exported: {ts}*\n")

    bms = items.get("bookmarks", [])
    nts = items.get("notes", [])
    hls = items.get("highlights", [])

    if bms:
        out.append("## Bookmarks\n")
        for b in sorted(bms, key=lambda x: x["line"]):
            out.append(f"**Line {b['line']+1}** (Page {b['page']+1})")
            if b.get("note"): out.append(f"> {b['note']}")
            out.append("")

    if nts:
        out.append("## Notes\n")
        for n in sorted(nts, key=lambda x: x["line"]):
            out.append(f"**Line {n['line']+1}**")
            if n.get("note"): out.append(f"> {n['note']}")
            out.append("")

    if hls:
        out.append("## Highlights\n")
        for h in sorted(hls, key=lambda x: x["start_line"]):
            out.append(_highlight_label(h))
            if h.get("note"): out.append(f"> {h['note']}")
            out.append("")

    return "\n".join(out)

# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

class LineInfo:
    __slots__ = ("abs_y", "page_num", "text")
    def __init__(self, abs_y, page_num, text=""):
        self.abs_y = abs_y; self.page_num = page_num; self.text = text

class PDFDocument:
    def __init__(self, filepath, zoom=1.5, page_gap=30):
        self.filepath   = str(filepath)
        self.zoom       = zoom
        self.page_gap   = page_gap
        self.doc        = fitz.open(filepath)
        self.page_pixmaps:     list[Optional[QPixmap]] = []
        self.page_pixmaps_inv: list[Optional[QPixmap]] = []
        self.page_offsets: list[int]  = []
        self.page_sizes:   list[tuple]= []
        self.lines:        list[LineInfo] = []
        self.total_height  = 0
        self.max_width     = 0
        first = self.doc[0].rect if self.doc else fitz.Rect(0,0,612,792)
        self.natural_width  = float(first.width)
        self.natural_height = float(first.height)
        self._parse()

    def _parse(self):
        cy = 0
        for pn, page in enumerate(self.doc):
            r   = page.rect
            w   = int(r.width  * self.zoom)
            h   = int(r.height * self.zoom)
            self.page_sizes.append((w, h))
            self.page_offsets.append(cy)
            self.page_pixmaps.append(None)
            self.page_pixmaps_inv.append(None)
            self.max_width = max(self.max_width, w)

            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0: continue
                for line in block.get("lines", []):
                    text = " ".join(s["text"] for s in line.get("spans",[])).strip()
                    if text:
                        self.lines.append(LineInfo(
                            cy + line["bbox"][1] * self.zoom, pn, text))
            cy += h + self.page_gap

        self.total_height = cy
        self.lines.sort(key=lambda l: l.abs_y)

    def render_page(self, pn: int) -> QPixmap:
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = self.doc[pn].get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height,
                     pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)

    def render_page_inv(self, pn: int) -> QPixmap:
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = self.doc[pn].get_pixmap(matrix=mat, alpha=False)
        pix.invert_irect()
        img = QImage(pix.samples, pix.width, pix.height,
                     pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)

    def render_range(self, start: int, end: int, inverted: bool = False):
        start = max(0, start)
        end   = min(len(self.doc) - 1, end)
        for pn in range(start, end + 1):
            if self.page_pixmaps[pn] is None:
                self.page_pixmaps[pn] = self.render_page(pn)
            if inverted and self.page_pixmaps_inv[pn] is None:
                self.page_pixmaps_inv[pn] = self.render_page_inv(pn)

    def placeholder(self, pn: int) -> QPixmap:
        w, h = self.page_sizes[pn]
        pm   = QPixmap(w, h)
        pm.fill(QColor(38, 38, 38))
        return pm

    def get_pixmap(self, pn: int, inverted: bool = False) -> QPixmap:
        if inverted:
            pm = self.page_pixmaps_inv[pn]
            return pm if pm is not None else self.placeholder(pn)
        pm = self.page_pixmaps[pn]
        return pm if pm is not None else self.placeholder(pn)

    @property
    def page_count(self):
        return len(self.doc)

class RenderThread(QThread):
    page_ready     = pyqtSignal(int, QPixmap)
    page_ready_inv = pyqtSignal(int, QPixmap)

    def __init__(self, document: PDFDocument, start_page: int, preload_inv: bool = True):
        super().__init__()
        self.document    = document
        self.start_page  = start_page
        self.preload_inv = preload_inv
        self._cancel     = False

    def cancel(self):
        self._cancel = True

    def run(self):
        n     = self.document.page_count
        order = _render_order(self.start_page, n)
        for pn in order:
            if self._cancel: return
            if self.document.page_pixmaps[pn] is None:
                try:
                    pm = self.document.render_page(pn)
                    self.document.page_pixmaps[pn] = pm
                    self.page_ready.emit(pn, pm)
                except Exception:
                    pass
        if self.preload_inv:
            for pn in order:
                if self._cancel: return
                if self.document.page_pixmaps_inv[pn] is None:
                    try:
                        pm_inv = self.document.render_page_inv(pn)
                        self.document.page_pixmaps_inv[pn] = pm_inv
                        self.page_ready_inv.emit(pn, pm_inv)
                    except Exception:
                        pass

def _render_order(start: int, total: int) -> list[int]:
    seen, order = set(), []
    for delta in range(total):
        for pn in [start + delta, start - delta]:
            if 0 <= pn < total and pn not in seen:
                seen.add(pn); order.append(pn)
    return order

# ---------------------------------------------------------------------------
# TranslateThread
# ---------------------------------------------------------------------------

class TranslateThread(QThread):
    result_ready = pyqtSignal(str)

    def __init__(self, text: str, target_lang: str, config: 'Config'):
        super().__init__()
        self.text        = text
        self.target_lang = target_lang
        self.config      = config

    def run(self):
        try:
            result = self._translate()
            self.result_ready.emit(result)
        except Exception as ex:
            self.result_ready.emit(f"[error: {ex}]")

    def _translate(self) -> str:
        provider = (self.config.get("translate_provider") or "anthropic").lower()
        text     = self.text.strip()
        lang     = self.target_lang

        if provider == "anthropic":
            return self._call_anthropic(text, lang)
        elif provider == "google":
            return self._call_google_unofficial(text, lang)
        elif provider == "google_official":
            return self._call_google_official(text, lang)
        elif provider == "openai":
            return self._call_openai(text, lang)
        elif provider == "ollama":
            return self._call_ollama(text, lang)
        else:
            return f"[unknown provider: {provider}]"

    def _call_anthropic(self, text: str, lang: str) -> str:
        import urllib.request, json
        key = self.config.get("translate_api_key") or ""
        if not key:
            return "[set translate_api_key to use Anthropic]"
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 256,
            "system": f"Translate to {lang}. Reply with only the translation, no explanation.",
            "messages": [{"role": "user", "content": text}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return data["content"][0]["text"].strip()

    def _call_google_unofficial(self, text: str, lang: str) -> str:
        import urllib.request, urllib.parse, json
        params = urllib.parse.urlencode({
            "client": "gtx", "sl": "auto", "tl": lang,
            "dt": "t", "q": text
        })
        url = f"https://translate.googleapis.com/translate_a/single?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return "".join(part[0] for part in data[0] if part[0]).strip()

    def _call_google_official(self, text: str, lang: str) -> str:
        import urllib.request, urllib.parse, json
        key = self.config.get("translate_api_key") or ""
        if not key:
            return "[set translate_api_key to use Google official API]"
        params = urllib.parse.urlencode({"key": key, "q": text, "target": lang})
        url    = f"https://translation.googleapis.com/language/translate/v2?{params}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        return data["data"]["translations"][0]["translatedText"].strip()

    def _call_openai(self, text: str, lang: str) -> str:
        import urllib.request, json
        key = self.config.get("translate_api_key") or ""
        if not key:
            return "[set translate_api_key to use OpenAI]"
        payload = json.dumps({
            "model": "gpt-4o-mini",
            "max_tokens": 256,
            "messages": [
                {"role": "system",
                 "content": f"Translate to {lang}. Reply with only the translation."},
                {"role": "user", "content": text}
            ]
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()

    def _call_ollama(self, text: str, lang: str) -> str:
        import urllib.request, json
        host  = (self.config.get("translate_ollama_host") or "http://localhost:11434").rstrip("/")
        model = self.config.get("translate_ollama_model") or "qwen3:8b"
        payload = json.dumps({
            "model": model,
            "prompt": f"Translate to {lang}. Reply with only the translation.\n\n{text}",
            "stream": False
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        return data.get("response", "").strip()


# ---------------------------------------------------------------------------
# AIThread for command family
# ---------------------------------------------------------------------------

class AIThread(QThread):
    result_ready = pyqtSignal(str)

    def __init__(self, prompt: str, provider: str, api_key: str, model: str):
        super().__init__()
        self.prompt = prompt
        self.provider = provider.lower()
        self.api_key = api_key
        self.model = model

    def run(self):
        try:
            if self.provider == "anthropic":
                result = self._call_anthropic()
            elif self.provider == "openai":
                result = self._call_openai()
            else:
                result = f"Unsupported AI provider: {self.provider}"
            self.result_ready.emit(result)
        except Exception as ex:
            self.result_ready.emit(f"[error: {ex}]")

    def _call_anthropic(self) -> str:
        import urllib.request, json
        if not self.api_key:
            return "[set ai_api_key to use Anthropic]"
        payload = json.dumps({
            "model": self.model,
            "max_tokens": 1024,
            "system": "You are a literary analysis assistant. Provide concise, insightful responses.",
            "messages": [{"role": "user", "content": self.prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        return data["content"][0]["text"].strip()

    def _call_openai(self) -> str:
        import urllib.request, json
        if not self.api_key:
            return "[set ai_api_key to use OpenAI]"
        payload = json.dumps({
            "model": self.model,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": "You are a literary analysis assistant. Provide concise, insightful responses."},
                {"role": "user", "content": self.prompt}
            ]
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()
        
# ---------------------------------------------------------------------------
# Reader Widget (continued from Part 1)
# ---------------------------------------------------------------------------

class WizardOverlay:
    """Paints a bottom-strip config wizard over the reader. All changes apply live."""

    APP_STEPS = [
        ("current_swatch",    "Colour swatch",        "swatch",  None,           0,   0,   1),
        ("current_font_idx",  "UI font",               "font",    None,           0,   0,   1),
        ("ui_font_offset",    "Font size",             "int",     None,          -6,  10,   1),
        ("top_bar_h",         "Top bar height",        "int",     None,          16,  60,   2),
        ("bottom_bar_h",      "Bottom bar height",     "int",     None,          16,  60,   2),
        ("panel_w",           "Panel width",           "int",     None,          80, 400,   5),
        ("ui_border_width",   "Border thickness",      "int",     None,           1,  10,   1),
        ("margin_side",       "Margin side",           "choice",  ["right","left"],0,  0,   1),
        ("progress_bar_style","Progress bar style",    "choice",  ["full","mini"], 0,  0,   1),
        ("library_dir",       "Library folder",        "path",    None,           0,   0,   0),
        ("translate_provider","Translate provider",    "choice",
            ["anthropic","google","google_official","openai","ollama"], 0, 0, 1),
        ("translate_api_key", "API key",               "text",    None,           0,   0,   0),
        ("translate_target_lang","Translate language", "lang",    None,           0,   0,   0),
    ]

    BOOK_STEPS = [
        ("pdf_invert",        "Invert PDF colors",     "bool",    None,           0,   1,   1),
        ("zoom_mode",         "Zoom mode",             "choice",
            ["fit-width","fit-page","50%","75%","100%","110%","120%"], 0, 0, 1),
        ("indicator_color",   "Highlight color",       "color",   None,           0,   0,   5),
        ("highlight_height",  "Highlight line height", "int",     None,           4,  60,   2),
        ("highlight_offset",  "Highlight offset",      "int",     None,         -20,  20,   1),
        ("highlight_alpha",   "Highlight opacity",     "int",     None,           0, 255,  10),
        ("margin_side",       "Margin side",           "choice",  ["right","left"],0,  0,   1),
    ]

    LANG_OPTIONS = [
        ("es", "Spanish"), ("fr", "French"), ("de", "German"), ("it", "Italian"),
        ("pt", "Portuguese"), ("zh", "Chinese"), ("ja", "Japanese"), ("ko", "Korean"),
        ("ru", "Russian"), ("ar", "Arabic"), ("hi", "Hindi"), ("nl", "Dutch"),
        ("pl", "Polish"), ("tr", "Turkish"), ("other", "Other…"),
    ]

    def __init__(self, kind: str, reader: 'ReaderWidget'):
        self.kind    = kind
        self.reader  = reader
        self.steps   = self.APP_STEPS if kind == "app" else self.BOOK_STEPS
        self.idx     = 0
        self.done    = False

    def current(self):
        return self.steps[self.idx]

    def get_val(self):
        key = self.current()[0]
        if self.kind == "book" and self.reader.document:
            e  = self.reader.history._entry(self.reader.document.filepath)
            ov = e.get("config_overrides", {})
            if key in ov: return ov[key]
            if key == "pdf_invert": return e.get("pdf_invert", False)
        return self.reader.config.get(key)

    def adjust(self, direction: int):
        key, label, typ, choices, mn, mx, step = self.current()
        cur = self.get_val()
        if typ == "swatch":
            idx = SWATCH_NAMES.index(cur) if cur in SWATCH_NAMES else 0
            new = SWATCH_NAMES[(idx + direction) % len(SWATCH_NAMES)]
            self.reader.config.set(key, new)
            _apply_swatch(new, self.reader.config)
            self.reader._update_cmd_style()
        elif typ == "font":
            fonts = _scan_fonts()
            if fonts:
                fidx = int(cur or 0)
                fidx = (fidx + direction) % len(fonts)
                fam  = _load_font_by_path(fonts[fidx])
                _UI_FONT_FAMILY_ref[0] = fam
                self.reader.config.set(key, str(fidx))
                self.reader._update_cmd_style()
        elif typ == "lang":
            codes = [c for c, _ in self.LANG_OPTIONS]
            cur_code = str(self.reader.config.get(key) or "")
            cidx = codes.index(cur_code) if cur_code in codes else 0
            new_code = codes[(cidx + direction) % len(codes)]
            if new_code != "other":
                self.reader.config.set(key, new_code)
        elif typ == "choice" and choices:
            cidx = choices.index(cur) if cur in choices else 0
            new  = choices[(cidx + direction) % len(choices)]
            self._apply(key, new)
        elif typ == "bool":
            self._apply(key, not bool(cur))
        elif typ == "int":
            self._apply(key, max(int(mn), min(int(mx), int(cur or mn) + direction * int(step))))
        elif typ == "color":
            c = QColor(cur or "#ffb000")
            h, s, v, _ = c.getHsvF()
            self._apply(key, QColor.fromHsvF((h + direction * step/360) % 1.0, s, v).name())
        self.reader.update()

    def _apply(self, key: str, val):
        if self.kind == "book" and self.reader.document and key not in ("zoom_mode",):
            fp = self.reader.document.filepath
            e  = self.reader.history._entry(fp)
            if key == "pdf_invert":
                e["pdf_invert"] = val
                _pdf_invert_ref[0] = bool(val)
                self.reader.history._save()
            else:
                e.setdefault("config_overrides", {})[key] = val
                self.reader.history._save()
        else:
            self.reader.config.set(key, val)
            global TOP_BAR_H, BOTTOM_BAR_H, PANEL_W
            if key == "top_bar_h":      TOP_BAR_H    = int(val)
            elif key == "bottom_bar_h": BOTTOM_BAR_H = int(val)
            elif key == "panel_w":      PANEL_W      = int(val)
            elif key == "ui_font_offset":
                _UI_FONT_OFFSET_ref[0] = int(val)
                self.reader._update_cmd_style()
            if key == "zoom_mode" and self.reader.document:
                self.reader.zoom_mode = val
                self.reader._rerender()

    def advance(self):
        if self.idx < len(self.steps) - 1:
            self.idx += 1
        else:
            self.done = True

    def back(self):
        if self.idx > 0:
            self.idx -= 1

    def paint(self, painter: QPainter, w: int, h: int):
        n      = len(self.steps)
        vp     = self.reader._vp()
        ox     = vp.left()
        ow     = vp.width()
        oh     = int(vp.height() * 0.38)
        oy     = vp.bottom() - oh

        font_b = _ui_font(10, bold=True)
        font   = _ui_font(9)
        font_s = _ui_font(8)
        row_h  = 28
        rows_visible = max(1, (oh - 48) // row_h)

        bg = QColor(UI_BG); bg.setAlpha(245)
        painter.fillRect(QRect(ox, oy, ow, oh), bg)
        painter.setPen(_mk_pen(AMBER_BRIGHT, 2))
        painter.drawLine(ox, oy, ox + ow, oy)

        kind_lbl = "APP SETUP" if self.kind == "app" else "BOOK SETUP"
        painter.setPen(AMBER_DIM)
        painter.setFont(font_s)
        painter.drawText(ox + 12, oy + 16, kind_lbl)
        painter.drawText(ox + ow - 60, oy + 16, f"{self.idx+1} of {n}")

        half    = rows_visible // 2
        start   = max(0, min(self.idx - half, n - rows_visible))
        end     = min(n, start + rows_visible)
        row_y   = oy + 26

        for i in range(start, end):
            key, label, typ, choices, mn, mx, step = self.steps[i]
            cur      = self._get_val_for(i)
            selected = (i == self.idx)
            val_str  = self._val_display(key, typ, choices, cur)

            if selected:
                painter.fillRect(QRect(ox + 4, row_y, ow - 8, row_h - 2), AMBER_VERY_DIM)
                painter.setPen(AMBER_BRIGHT)
            else:
                painter.setPen(AMBER_DIM)

            painter.setFont(font_b if selected else font)
            painter.drawText(ox + 12, row_y + row_h - 9, label)

            painter.setFont(font_b if selected else font_s)
            fm   = QFontMetrics(painter.font())
            vx   = ox + ow - fm.horizontalAdvance(val_str) - 12
            painter.drawText(vx, row_y + row_h - 9, val_str)

            if selected and typ not in ("text", "path"):
                painter.setPen(AMBER_DARK)
                painter.setFont(font_s)
                painter.drawText(ox + 12 + QFontMetrics(font_b).horizontalAdvance(label) + 8,
                                 row_y + row_h - 9, "◀  ▶")
            row_y += row_h

        fy = oy + oh - 8
        painter.setPen(AMBER_DARK)
        painter.setFont(font_s)
        key, label, typ, choices, mn, mx, step = self.current()
        if typ == "lang":
            painter.drawText(ox + 12, fy, "←/→ common languages   for others: set translate_target_lang <code>   ↑/↓ move   Enter next   Esc close")
        elif typ in ("text", "path"):
            painter.drawText(ox + 12, fy, "Edit via :set command   Enter/↓ next   ↑ back   Esc close")
        else:
            painter.drawText(ox + 12, fy, "←/→ change   ↑/↓ move   Enter next   Esc close")

        dot_total_w = n * 10 - 2
        dx = ox + (ow - dot_total_w) // 2
        dy = oy + oh - 20
        for i in range(n):
            c = AMBER_BRIGHT if i == self.idx else AMBER_VERY_DIM
            painter.fillRect(QRect(dx + i*10, dy, 8, 4), c)

    def _get_val_for(self, step_idx: int):
        key = self.steps[step_idx][0]
        if self.kind == "book" and self.reader.document:
            e  = self.reader.history._entry(self.reader.document.filepath)
            ov = e.get("config_overrides", {})
            if key in ov: return ov[key]
            if key == "pdf_invert": return e.get("pdf_invert", False)
        return self.reader.config.get(key)

    def _val_display(self, key, typ, choices, cur) -> str:
        if typ == "swatch":   return str(cur or "amber")
        if typ == "font":
            fonts = _scan_fonts()
            idx   = int(cur or 0)
            return os.path.basename(fonts[idx]) if fonts and idx < len(fonts) else "default"
        if typ == "bool":     return "ON" if cur else "OFF"
        if typ == "color":    return str(cur or "#ffb000")
        if typ == "choice":   return str(cur or (choices[0] if choices else ""))
        if typ == "lang":
            code = str(cur or "")
            match = next((name for c, name in self.LANG_OPTIONS if c == code), None)
            return f"{match} ({code})" if match and code else "(not set)"
        if typ == "text":     return str(cur or "(not set)")
        if typ == "path":     return str(cur or "(not set)")
        return str(cur or "")


class ReaderWidget(QWidget):
    def __init__(self, config: Config, history: History):
        super().__init__()
        self.config   = config
        self.history  = history
        self.document: Optional[PDFDocument] = None
        self.current_line    = 0
        self.command_mode    = False
        self.status_text     = ""
        self.zoom_mode: str  = str(self.config.get("zoom_mode") or "fit-width")
        self.panel: Optional[dict]     = None
        self._panel_scroll: int        = 0
        self._pending: Optional[dict]  = None
        self._panel_rects: list        = []
        self._render_thread: Optional[RenderThread] = None
        self._cmd_history:   list[str] = []
        self._cmd_history_idx: int     = -1
        self._line_history:  list[int] = []
        self._panel_mode: Optional[str] = None
        self._panel_cursor: int        = 0
        self._pre_panel_line: int      = 0
        self._search_term: str         = ''
        self._search_re:    Optional[re.Pattern] = None
        self._last_command: str        = ''
        # AI state
        self._ai_select_mode: Optional[dict] = None
        self._ai_result_panel: Optional[dict] = None
        self._last_ai_selection: Optional[dict] = None
        self._ai_thread: Optional[QThread] = None
        self._ai_select_scroll: int = 0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(600, 400)
        self.setMouseTracking(False)
        self._cmd_cooldown = 0.0

        self.cmd = QLineEdit(self)
        self.cmd.setVisible(False)
        self.cmd.installEventFilter(self)
        self._update_cmd_style()

        self._translate_mode      = False
        self._translate_word_idx  = 0
        self._translate_word_xs   = []
        self._translate_result    = ""
        self._translate_fetching  = False
        self._translate_anim_frame= 0
        self._translate_thread: Optional[TranslateThread] = None
        self._translate_anim_timer = QTimer(self)
        self._translate_anim_timer.timeout.connect(self._translate_anim_tick)
        self._translate_anim_timer.start(130)

        self._wizard: Optional[WizardOverlay] = None

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self.cmd and event.type() == QEvent.Type.KeyPress:
            k = event.key()
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._execute_command()
                return True
            if k == Qt.Key.Key_Escape:
                self._exit_command_mode()
                return True
            if k == Qt.Key.Key_Up and self._cmd_history:
                if self._cmd_history_idx < len(self._cmd_history) - 1:
                    self._cmd_history_idx += 1
                entry = self._cmd_history[-(self._cmd_history_idx + 1)]
                self.cmd.setText(":" + entry)
                self.cmd.setCursorPosition(len(self.cmd.text()))
                return True
            if k == Qt.Key.Key_Down and self._cmd_history:
                if self._cmd_history_idx > 0:
                    self._cmd_history_idx -= 1
                    entry = self._cmd_history[-(self._cmd_history_idx + 1)]
                    self.cmd.setText(":" + entry)
                else:
                    self._cmd_history_idx = -1
                    self.cmd.setText(":")
                self.cmd.setCursorPosition(len(self.cmd.text()))
                return True
        return super().eventFilter(obj, event)

    def _update_cmd_style(self):
        sz  = max(6, 10 + _UI_FONT_OFFSET_ref[0])
        fam = _UI_FONT_FAMILY_ref[0]
        inv_bg = AMBER_INV_BG.name()
        inv_fg = AMBER_INV_FG.name()
        self.cmd.setStyleSheet(f"""
            QLineEdit {{
                background-color: {inv_bg};
                color: {inv_fg};
                border: none;
                padding: 3px 10px;
                font-family: "{fam}", "Courier New", monospace;
                font-size: {sz}px;
                selection-background-color: {inv_fg};
                selection-color: {inv_bg};
            }}
        """)

    def resizeEvent(self, ev):
        self.cmd.setGeometry(0, self.height()-BOTTOM_BAR_H, self.width(), BOTTOM_BAR_H)
        self.status_text = f"{self.width()} × {self.height()}"
        if hasattr(self, '_resize_timer'):
            self._resize_timer.stop()
        else:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._clear_status)
        self._resize_timer.start(1500)
        super().resizeEvent(ev)

    def _enter_command_mode(self):
        if time.time() < self._cmd_cooldown:
            return
        self._cmd_history_idx = -1
        self._update_cmd_style()
        self.command_mode = True
        self.cmd.setVisible(True)
        self.cmd.setText(":")
        self.cmd.setFocus()
        self.cmd.setCursorPosition(len(self.cmd.text()))
        self.update()

    def _exit_command_mode(self):
        self.command_mode = False
        self._cmd_cooldown = time.time() + 0.15
        self.cmd.setVisible(False)
        self.cmd.clear()
        self.setFocus()
        self.update()

    def _cfg(self, key):
        if self.document:
            ov = self.history._entry(self.document.filepath).get("config_overrides", {})
            if key in ov: return ov[key]
        return self.config.get(key)

    # ---------------------------------------------------------------- load

    def load_document(self, filepath: str):
        filepath = os.path.expanduser(filepath)
        if not os.path.exists(filepath):
            self.status_text = f"file not found: {filepath}"; self.update(); return

        self._stop_render_thread()
        try:
            zoom = self._compute_zoom(self.zoom_mode, peek_path=filepath)
            doc  = PDFDocument(filepath, zoom=zoom, page_gap=int(self.config.get("page_gap")))
            zoom2 = self._compute_zoom_for_doc(self.zoom_mode, doc)
            if abs(zoom2 - zoom) > 0.01:
                doc = PDFDocument(filepath, zoom=zoom2, page_gap=int(self.config.get("page_gap")))

            meta = doc.doc.metadata
            e    = self.history._entry(filepath)
            is_new_book = e.get("status") == "unread" and e.get("line", 0) == 0 and e.get("total_lines") is None
            if meta.get("title")  and not e.get("title"):  e["title"]  = meta["title"]
            if meta.get("author") and not e.get("author"): e["author"] = meta["author"]
            self.history.set_totals(filepath, len(doc.lines), doc.page_count)

            self.document     = doc
            self.current_line = min(self.history.get_line(filepath), max(0, len(doc.lines)-1))
            self.panel        = None
            self._pending     = None
            _pdf_invert_ref[0] = bool(self.history._entry(filepath).get("pdf_invert", False))

            cur_page  = doc.lines[self.current_line].page_num if doc.lines else 0
            eager     = int(self.config.get("eager_pages") or 2)
            preload   = bool(self.config.get("preload_inverted") if self.config.get("preload_inverted") is not None else True)
            doc.render_range(cur_page - eager, cur_page + eager, inverted=preload)
            self.update()
            self._start_render_thread(cur_page)
            if is_new_book:
                QTimer.singleShot(100, lambda: self._open_wizard("book"))
        except Exception as ex:
            self.status_text = f"error: {ex}"; self.update()

    def _start_render_thread(self, start_page: int):
        self._stop_render_thread()
        preload = bool(self.config.get("preload_inverted") if self.config.get("preload_inverted") is not None else True)
        t = RenderThread(self.document, start_page, preload_inv=preload)
        t.page_ready.connect(self._on_page_ready)
        t.page_ready_inv.connect(self._on_page_ready_inv)
        t.finished.connect(self._on_render_done)
        self._render_thread = t
        t.start()

    def _stop_render_thread(self):
        if self._render_thread is not None:
            self._render_thread.cancel()
            self._render_thread.wait(200)
            self._render_thread = None

    def _on_page_ready(self, pn: int, pixmap: QPixmap):
        if self.document:
            self.document.page_pixmaps[pn] = pixmap
            if not _pdf_invert_ref[0]:
                self._repaint_if_visible(pn)

    def _on_page_ready_inv(self, pn: int, pixmap: QPixmap):
        if self.document:
            self.document.page_pixmaps_inv[pn] = pixmap
            if _pdf_invert_ref[0]:
                self._repaint_if_visible(pn)

    def _repaint_if_visible(self, pn: int):
        if not self.document: return
        scroll = self._scroll_offset()
        vp     = self._vp()
        py     = vp.y() + self.document.page_offsets[pn] - scroll
        _, ph  = self.document.page_sizes[pn]
        if py + ph >= vp.y() and py <= vp.bottom():
            self.update()

    def _on_render_done(self):
        self._render_thread = None
        self.update()

    # ---------------------------------------------------------------- zoom

    def _compute_zoom_for_doc(self, mode: str, doc: 'PDFDocument') -> float:
        nw, nh = doc.natural_width, doc.natural_height
        vp = self._vp()
        uw = vp.width()  - 40
        uh = vp.height() - 40
        if mode == "fit-width": return max(0.1, uw / nw)
        if mode == "fit-page":  return max(0.1, uh / nh)
        if mode == "50%":  return 0.75
        if mode == "75%":  return 1.1
        if mode == "100%": return 1.5
        return float(self.config.get("zoom_fixed") or 1.5)

    def _compute_zoom(self, mode, peek_path=None):
        nw, nh = 612.0, 792.0
        if self.document:
            nw, nh = self.document.natural_width, self.document.natural_height
        elif peek_path and os.path.exists(peek_path):
            try:
                d = fitz.open(peek_path); r = d[0].rect; nw, nh = r.width, r.height; d.close()
            except Exception: pass
        vp = self._vp()
        uw = vp.width()  - 40
        uh = vp.height() - 40
        if mode == "fit-width": return max(0.1, uw/nw)
        if mode == "fit-page":  return max(0.1, uh/nh)
        if mode == "50%":  return 0.75
        if mode == "75%":  return 1.1
        if mode == "100%": return 1.5
        return float(self.config.get("zoom_fixed") or 1.5)

    def _rerender(self):
        if not self.document: return
        old_line = self.current_line
        fp       = self.document.filepath
        self._stop_render_thread()
        try:
            self.load_document(fp)
            if self.document:
                self.current_line = min(old_line, max(0, len(self.document.lines)-1))
        except Exception as ex:
            self.status_text = f"zoom error: {ex}"
        self.update()

    # -------------------------------------------------------------- helpers

    def _margin_side(self) -> str:
        return str(self.config.get("margin_side") or "right")

    def _bw(self) -> int:
        return max(1, int(self.config.get("ui_border_width") or 2))

    def _vp(self) -> QRect:
        side = self._margin_side()
        if side == "left":
            return QRect(PANEL_W, TOP_BAR_H,
                         self.width() - PANEL_W,
                         self.height() - TOP_BAR_H - BOTTOM_BAR_H)
        else:
            return QRect(0, TOP_BAR_H,
                         self.width() - PANEL_W,
                         self.height() - TOP_BAR_H - BOTTOM_BAR_H)

    def _usable_h(self):
        return self._vp().height()

    def _midpoint_y(self):
        return self._vp().height() * float(self.config.get("midpoint"))

    def _lh(self):   return max(8, int(self._cfg("highlight_height") or 20))
    def _voff(self): return int(self._cfg("highlight_offset") or 0)

    def _scroll_offset(self):
        if not self.document or not self.document.lines: return 0.0
        ly = self.document.lines[self.current_line].abs_y
        mp = self._midpoint_y()
        if ly < mp: return 0.0
        return min(ly - mp, max(0, self.document.total_height - self._usable_h()))

    def _indicator_screen_y(self):
        vp = self._vp()
        if not self.document or not self.document.lines:
            return vp.y() + int(self._midpoint_y())
        return vp.y() + int(self.document.lines[self.current_line].abs_y - self._scroll_offset())

    def _page_x_offset(self):
        if not self.document: return L_MARGIN
        vp = self._vp()
        return vp.x() + max(0, (vp.width() - self.document.max_width) // 2)

    def _lines_per_screen(self):
        if not self.document or not self.document.lines: return 10
        avg = self.document.total_height / len(self.document.lines)
        return max(1, int(self._usable_h() / avg) - 2)

    def _metar(self) -> str:
        if not self.document: return ""
        e     = self.history._entry(self.document.filepath)
        total = len(self.document.lines)
        cur   = self.current_line
        pct   = int(cur / max(total, 1) * 100)
        n  = len(e.get("notes", []))
        b  = len(e.get("bookmarks", []))
        h  = len(e.get("highlights", []))
        return f"N{n}B{b}H{h}R{pct}P{len(self.document.doc)}"

    def _push_history(self, line: int):
        if self._line_history and self._line_history[-1] == line:
            return
        self._line_history.append(line)
        if len(self._line_history) > 50:
            self._line_history.pop(0)

    def _pop_history(self) -> Optional[int]:
        if self._line_history:
            return self._line_history.pop()
        return None

    def _resolve_line_range(self, rs: RangeSpec) -> tuple:
        total = len(self.document.lines)
        cur   = self.current_line
        if rs.mode == "current":   return cur, cur
        if rs.mode == "absolute":  return max(0, rs.start-1), min(total-1, rs.end-1)
        if rs.mode == "relative":  return max(0, cur-rs.back), min(total-1, cur+rs.fwd)
        return cur, cur

    def _resolve_page_range(self, rs: RangeSpec) -> tuple:
        tp = self.document.page_count
        cp = self.document.lines[self.current_line].page_num if self.document.lines else 0
        if rs.mode == "current":   return cp, cp
        if rs.mode == "absolute":  return max(0, rs.start-1), min(tp-1, rs.end-1)
        if rs.mode == "relative":  return max(0, cp-rs.back), min(tp-1, cp+rs.fwd)
        return cp, cp

    def _page_range_to_line_range(self, sp, ep) -> tuple:
        lines = self.document.lines; total = len(lines)
        sl = next((i for i, l in enumerate(lines) if l.page_num == sp), 0)
        el = 0
        for i in range(total-1, -1, -1):
            if lines[i].page_num == ep: el = i; break
        return sl, el

    # -------------------------------------------------------------- paint (partial – continued in Part 3)
    # Due to length, the remaining paint methods and all of keyPressEvent, wheelEvent,
    # AI methods, command handling, etc. will be in Part 3.


    # -------------------------------------------------------------- paint (continued)

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), UI_BG)

        if not self.document:
            self._paint_top_bar(painter)
            self._paint_bottom_bar(painter)
            painter.setPen(AMBER_DARK)
            painter.setFont(_ui_font(13))
            vp = self._vp()
            painter.drawText(vp, Qt.AlignmentFlag.AlignCenter,
                             "ENTER COMMAND MODE AND TYPE:  OPEN <PATH>")
            self._paint_frame(painter)
            if self._wizard:
                self._wizard.paint(painter, self.width(), self.height())
            return

        scroll = self._scroll_offset()
        px     = self._page_x_offset()
        lh     = self._lh()
        voff   = self._voff()
        vp     = self._vp()
        dlines = self.document.lines
        total  = len(dlines)

        inv = _pdf_invert_ref[0]
        for i in range(self.document.page_count):
            py = vp.y() + self.document.page_offsets[i] - scroll
            pw, ph = self.document.page_sizes[i]
            if py + ph >= vp.y() and py <= vp.bottom():
                painter.drawPixmap(px, int(py), self.document.get_pixmap(i, inverted=inv))

        sh_col = QColor(self._cfg("saved_highlight_color") or AMBER.name())
        sh_col.setAlpha(int(self._cfg("saved_highlight_alpha") or 30))
        for hh in self.history._entry(self.document.filepath).get("highlights", []):
            sl, el = hh.get("start_line",0), hh.get("end_line",0)
            if sl >= total or el >= total: continue
            sy = int(vp.y() + dlines[sl].abs_y - scroll + voff)
            ey = int(vp.y() + dlines[el].abs_y - scroll + voff + lh)
            if ey >= vp.y() and sy <= vp.bottom():
                painter.fillRect(QRect(px, sy, self.document.max_width, ey-sy), sh_col)

        ind_y   = self._indicator_screen_y() + voff
        ind_col = QColor(self._cfg("indicator_color") or "#ffb000")
        ind_col.setAlpha(int(self._cfg("highlight_alpha") or 35))
        painter.fillRect(QRect(px, ind_y-2, self.document.max_width, lh), ind_col)

        if self._ai_select_mode:
            mr = self._margin_rect()
            self._paint_ai_selection(painter, mr)
        else:
            self._paint_margin(painter, scroll, voff, vp, dlines, total, lh, ind_y)

        mr = self._margin_rect()
        self._paint_scrollbar(painter, mr, scroll, vp)

        if hasattr(self, 'gamepad_ref') and self.gamepad_ref:
            hl_start, hl_end, hl_active = self.gamepad_ref.get_hl_range()
            if hl_active and self.document and self.document.lines:
                for li in range(hl_start, hl_end + 1):
                    if 0 <= li < len(self.document.lines):
                        ly = vp.y() + int(self.document.lines[li].abs_y) - int(scroll) + voff
                        if vp.top() <= ly <= vp.bottom():
                            oc = QColor(self._cfg("indicator_color") or "#ffb000")
                            oc.setAlpha(80)
                            painter.fillRect(QRect(px, ly, self.document.max_width, lh), oc)

        self._paint_frame(painter)
        self._paint_top_bar(painter)
        self._paint_bottom_bar(painter)
        self._paint_vu_meter(painter)

        if self.panel and self.panel.get("kind") == "help":
            self._paint_help_panel(painter)
        if self._pending:
            self._paint_confirm(painter)
        if getattr(self, '_config_popup_open', False):
            self._paint_config_popup(painter)
        if self._translate_mode or self._translate_fetching or self._translate_result:
            self._paint_translate_overlay(painter)
        if self._wizard:
            self._wizard.paint(painter, self.width(), self.height())
        if self._ai_result_panel and self._ai_result_panel.get('visible'):
            self._paint_ai_result(painter)

    def _paint_frame(self, painter: QPainter):
        w, h = self.width(), self.height()
        bw   = self._bw()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(QRect(0, 0, w, bw), AMBER_DARK)
        painter.fillRect(QRect(0, h-bw, w, bw), AMBER_DARK)
        painter.fillRect(QRect(0, bw, bw, h-bw*2), AMBER_DARK)
        painter.fillRect(QRect(w-bw, bw, bw, h-bw*2), AMBER_DARK)
        painter.setPen(AMBER)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRect(bw, bw, w-bw*2-1, h-bw*2-1))

    def _paint_top_bar(self, painter: QPainter):
        w = self.width()
        painter.fillRect(QRect(0, 0, w, TOP_BAR_H), UI_BG)
        painter.setPen(_mk_pen(AMBER_DARK, self._bw()))
        painter.drawLine(0, TOP_BAR_H-1, w, TOP_BAR_H-1)

        font_b = _ui_font(9, bold=True)
        font   = _ui_font(9)
        ty     = TOP_BAR_H - 8

        if self.document and self.document.lines:
            line  = self.document.lines[self.current_line]
            total = len(self.document.lines)
            left_txt = (f"LINE {self.current_line+1}/{total}"
                        f"  PAGE {line.page_num+1}/{self.document.page_count}"
                        f"  {self._metar()}")
            painter.setPen(AMBER)
            painter.setFont(font_b)
            painter.drawText(L_MARGIN + 6, ty, left_txt)

            e       = self.history._entry(self.document.filepath)
            title   = (e.get("title") or Path(self.document.filepath).stem)[:28]
            author  = (e.get("author") or "")[:20]
            centre_txt = f"{title}{',  '+author if author else ''}"
            painter.setPen(AMBER_BRIGHT)
            painter.setFont(font_b)
            fm  = QFontMetrics(font_b)
            cx  = (w - fm.horizontalAdvance(centre_txt)) // 2
            painter.drawText(cx, ty, centre_txt)

            mode_map = {None: "READING", "bookmarks": "BOOKMARK VIEW",
                        "notes": "NOTE VIEW", "highlights": "HIGHLIGHT VIEW",
                        "audionotes": "AUDIO NOTE VIEW"}
            if getattr(self, '_search_panel_active', False):
                mode_txt = "SEARCH VIEW MODE"
            else:
                mode_txt = mode_map.get(self._panel_mode, "READING") + " MODE"

            right_x = (w - PANEL_W - fm.horizontalAdvance(mode_txt) - 8
                       if self._margin_side() == "right" else PANEL_W + 8)
            painter.setPen(AMBER_DIM)
            painter.setFont(font)
            painter.drawText(right_x, ty, mode_txt)

            if getattr(self, '_search_panel_active', False):
                query    = getattr(self, '_search_query', "")
                q_active = getattr(self, '_search_input_active', False)
                q_txt    = f"  /{query}{'_' if q_active else ''}"
                q_x      = right_x + QFontMetrics(font).horizontalAdvance(mode_txt) + 12
                painter.setPen(AMBER_BRIGHT if q_active else AMBER_DIM)
                painter.setFont(font_b)
                painter.drawText(q_x, ty, q_txt)
        else:
            painter.setPen(AMBER_DIM)
            painter.setFont(font_b)
            painter.drawText(L_MARGIN + 6, ty, "SCROLLREADER")

    def _paint_bottom_bar(self, painter: QPainter):
        w, h = self.width(), self.height()
        y    = h - BOTTOM_BAR_H

        if self.command_mode:
            painter.fillRect(QRect(0, y, w, BOTTOM_BAR_H), AMBER_INV_BG)
        else:
            painter.fillRect(QRect(0, y, w, BOTTOM_BAR_H), UI_BG)
            painter.setPen(_mk_pen(AMBER_DARK, self._bw()))
            painter.drawLine(0, y, w, y)
            painter.setPen(AMBER_DIM)
            painter.setFont(_ui_font(8))

            if self._panel_mode:
                ref = "↑↓ NAVIGATE   SPACE/ENTER SELECT   TAB/ESC BACK"
            elif self._pending:
                ref = "Y CONFIRM   N/TAB/ESC CANCEL"
            elif self.status_text:
                ref = self.status_text
            else:
                ref = ("SPC/↓/S NEXT   ↑/TAB/W BACK   A/D PAGE   "
                       "L LIB   N/B/H PANELS   R READING   I INVERT   ? HELP   "
                       "CTRL++ ZOOM IN   CTRL+- ZOOM OUT   CTRL+U/I MIDPOINT   "
                       "CTRL+K/L SWATCH   CTRL+O/P IND.COLOR   = UNDO")
            painter.drawText(6, h - 8, ref)

    def _margin_indicator_y(self, line_idx: int, scroll: float, voff: int, vp: QRect) -> int:
        if not self.document or line_idx >= len(self.document.lines):
            return -999
        return int(vp.y() + self.document.lines[line_idx].abs_y - scroll + voff)

    def _margin_rect(self) -> QRect:
        side = self._margin_side()
        if side == "left":
            x = 0
        else:
            x = self.width() - PANEL_W
        return QRect(x, TOP_BAR_H, PANEL_W,
                     self.height() - TOP_BAR_H - BOTTOM_BAR_H)

    def _paint_margin(self, painter, scroll, voff, vp, dlines, total, lh, ind_y):
        mr   = self._margin_rect()
        side = self._margin_side()
        bw   = self._bw()
        e    = self.history._entry(self.document.filepath)

        painter.fillRect(mr, UI_BG)
        painter.setPen(_mk_pen(AMBER_DARK, self._bw()))
        if side == "right":
            painter.drawLine(mr.left(), mr.top(), mr.left(), mr.bottom())
        else:
            painter.drawLine(mr.right(), mr.top(), mr.right(), mr.bottom())

        if self._panel_mode:
            self._paint_panel_list(painter, mr, e, lh)
        elif getattr(self, '_search_panel_active', False):
            self._paint_search_panel(painter, mr)
        else:
            self._paint_margin_reading(painter, mr, scroll, voff, vp,
                                        dlines, total, lh, ind_y, e, side)

    def _paint_margin_reading(self, painter, mr, scroll, voff, vp,
                               dlines, total, lh, ind_y, e, side):
        IND_W   = 12
        TEXT_X  = mr.left() + IND_W + 6 if side == "right" else mr.left() + 6
        TEXT_W  = PANEL_W - IND_W - 10
        font    = _ui_font(8)
        fm      = QFontMetrics(font)
        painter.setFont(font)
        ind_color = QColor(self._cfg("indicator_color") or "#ffb000")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ind_color)
        ty = ind_y + lh // 2
        sz = 7
        if side == "right":
            tx = mr.left() + 2
            painter.drawPolygon(QPolygon([
                QPoint(tx+sz, ty-sz//2), QPoint(tx+sz, ty+sz//2), QPoint(tx, ty)
            ]))
        else:
            tx = mr.right() - sz - 2
            painter.drawPolygon(QPolygon([
                QPoint(tx, ty-sz//2), QPoint(tx, ty+sz//2), QPoint(tx+sz, ty)
            ]))

        items = []
        for bm in e.get("bookmarks", []):
            line = bm.get("line", 0)
            if line >= total: continue
            ay = self._margin_indicator_y(line, scroll, voff, vp)
            if vp.top() <= ay <= vp.bottom():
                items.append(("bookmark", ay, line, bm.get("note", "")))

        for nn in e.get("notes", []):
            line = nn.get("line", 0)
            if line >= total: continue
            ay = self._margin_indicator_y(line, scroll, voff, vp)
            if vp.top() <= ay <= vp.bottom():
                items.append(("note", ay, line, nn.get("note", "")))

        for hh in e.get("highlights", []):
            sl = hh.get("start_line", 0)
            if sl >= total: continue
            ay = self._margin_indicator_y(sl, scroll, voff, vp)
            if vp.top() <= ay <= vp.bottom():
                items.append(("highlight", ay, sl, hh.get("note", "")))

        items.sort(key=lambda x: x[1])
        last_y = -999
        for kind, ay, line, note in items:
            painter.setPen(Qt.PenStyle.NoPen)
            if kind == "bookmark":
                painter.setBrush(AMBER)
                if side == "right":
                    painter.drawPolygon(QPolygon([
                        QPoint(mr.left()+2, ay+lh//2),
                        QPoint(mr.left()+IND_W-2, ay+2),
                        QPoint(mr.left()+IND_W-2, ay+lh-2)
                    ]))
                else:
                    painter.drawPolygon(QPolygon([
                        QPoint(mr.right()-2, ay+lh//2),
                        QPoint(mr.left()+2, ay+2),
                        QPoint(mr.left()+2, ay+lh-2)
                    ]))
            elif kind == "note":
                painter.setPen(AMBER_DIM)
                painter.setBrush(AMBER_DARK)
                painter.drawRect(QRect(mr.left()+2, ay+lh//2-5, 9, 9))
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                painter.fillRect(QRect(mr.left()+2, ay, 4, max(2, lh)), AMBER_DIM)

            if note and ay > last_y + 4:
                painter.setPen(AMBER_DIM)
                painter.setFont(font)
                if kind == "note":
                    words = note.split()
                    cur_line = ""; ty = ay + lh
                    for word in words:
                        test = (cur_line + " " + word).strip()
                        if fm.horizontalAdvance(test) <= TEXT_W:
                            cur_line = test
                        else:
                            if cur_line:
                                painter.drawText(TEXT_X, ty, cur_line)
                                ty += fm.height() + 1
                            cur_line = word
                        if ty > vp.bottom(): break
                    if cur_line and ty <= vp.bottom():
                        painter.drawText(TEXT_X, ty, cur_line)
                    last_y = ty
                else:
                    truncated = fm.elidedText(note, Qt.TextElideMode.ElideRight, TEXT_W)
                    painter.drawText(TEXT_X, ay + lh - 3, truncated)
                    last_y = ay + lh

        painter.setPen(Qt.PenStyle.NoPen)
        for hh in e.get("highlights", []):
            sl, el = hh.get("start_line",0), hh.get("end_line",0)
            if sl >= total or el >= total: continue
            sy = self._margin_indicator_y(sl, scroll, voff, vp)
            ey = self._margin_indicator_y(el, scroll, voff, vp) + lh
            if ey >= vp.top() and sy <= vp.bottom():
                hx = mr.left() + 6
                painter.fillRect(QRect(hx, sy, 3, max(2, ey-sy)), AMBER_DIM)

    def _paint_panel_list(self, painter, mr, e, lh):
        items    = self._panel_items()
        font_b   = _ui_font(9, bold=True)
        font     = _ui_font(9)
        pw       = mr.width()
        px       = mr.left()
        py       = mr.top()

        painter.fillRect(QRect(px, py, pw, 4), AMBER_VERY_DIM)
        py += 8

        if not items:
            return

        fm_b = QFontMetrics(font_b)
        fm   = QFontMetrics(font)
        lh_line = fm.height() + 2

        for idx, item in enumerate(items):
            selected = (idx == self._panel_cursor)
            line_num = item.get("line", item.get("start_line", 0))
            note_txt = item.get("note", "") or ""

            if self._panel_mode == "highlights":
                loc_str = f"L{line_num+1}-{item.get('end_line',line_num)+1}"
            else:
                loc_str = f"L{line_num+1:05d}"

            note_lines = []
            if note_txt:
                words = note_txt.split()
                cur_line = ""
                for word in words:
                    test = (cur_line + " " + word).strip()
                    if fm.horizontalAdvance(test) <= pw - 16:
                        cur_line = test
                    else:
                        if cur_line: note_lines.append(cur_line)
                        cur_line = word
                if cur_line: note_lines.append(cur_line)

            item_h = 18 + len(note_lines) * lh_line + 2

            if py + item_h > mr.bottom() - 2: break

            if selected:
                painter.fillRect(QRect(px+2, py, pw-4, item_h), AMBER_INV_BG)
                fg = AMBER_INV_FG; fg2 = AMBER_INV_FG
            else:
                fg = AMBER; fg2 = AMBER_DIM

            painter.setPen(fg); painter.setFont(font_b)
            painter.drawText(px+8, py+14, loc_str)

            if note_lines:
                painter.setFont(font); painter.setPen(fg2)
                ty = py + 18
                for line in note_lines:
                    painter.drawText(px+8, ty + lh_line - 2, line)
                    ty += lh_line

            painter.setPen(_mk_pen(AMBER_VERY_DIM, self._bw()))
            painter.drawLine(px+4, py+item_h, px+pw-4, py+item_h)
            py += item_h

    def _adjust_zoom(self, delta: float):
        if not self.document: return
        cur_zoom = self.document.zoom
        new_zoom = round(max(0.3, min(5.0, cur_zoom + delta)), 2)
        if new_zoom == cur_zoom: return
        line = self.current_line
        self.config.set("zoom_fixed", new_zoom)
        self.config.set("zoom_mode", "fixed")
        self.zoom_mode = "fixed"
        self._rerender()
        self.current_line = min(line, max(0, len(self.document.lines)-1))
        self.status_text  = f"zoom: {int(new_zoom*100)}%"
        QTimer.singleShot(3000, self._clear_status)
        self.update()

    def _adj_dim(self, key: str, delta: int, mn: int, mx: int):
        global TOP_BAR_H, BOTTOM_BAR_H, PANEL_W
        cur = int(self.config.get(key) or {
            "top_bar_h": TOP_BAR_H, "bottom_bar_h": BOTTOM_BAR_H,
            "panel_w": PANEL_W}.get(key, mn))
        new_val = max(mn, min(mx, cur + delta))
        self.config.set(key, new_val)
        if key == "top_bar_h":    TOP_BAR_H    = new_val
        elif key == "bottom_bar_h": BOTTOM_BAR_H = new_val
        elif key == "panel_w":    PANEL_W      = new_val
        self.cmd.setGeometry(0, self.height()-BOTTOM_BAR_H, self.width(), BOTTOM_BAR_H)
        self.status_text = f"{key}: {new_val}"
        QTimer.singleShot(3000, self._clear_status)
        self.update()

    def _cycle_indicator_color(self, delta_deg: int, key: str = "indicator_color"):
        cur = QColor(self.config.get(key) or "#ffb000")
        h, s, v, _ = cur.getHsvF()
        new_h = (h + delta_deg / 360.0) % 1.0
        new_color = QColor.fromHsvF(new_h, max(0.6, s), max(0.7, v))
        hex_color = new_color.name()
        self.config.set(key, hex_color)
        self.status_text = f"{key}: {hex_color}"
        QTimer.singleShot(3000, self._clear_status)
        self.update()

    def _open_annot_panel(self, mode: str) -> Optional[str]:
        if not self.document: return "no document open"
        if self._panel_mode == mode:
            self._panel_mode = None
            self.current_line = self._pre_panel_line
            self.update()
            return None
        self._pre_panel_line = self.current_line
        self._panel_mode     = mode
        self._panel_cursor   = 0
        items = self._panel_items()
        if items:
            self.current_line = items[0].get("line", items[0].get("start_line", 0))
        self.update()
        return None

    def _panel_items(self) -> list:
        if not self.document: return []
        e = self.history._entry(self.document.filepath)
        if self._panel_mode == "bookmarks":
            return sorted(e.get("bookmarks", []), key=lambda x: x.get("line",0))
        if self._panel_mode == "notes":
            return sorted(e.get("notes", []), key=lambda x: x.get("line",0))
        if self._panel_mode == "highlights":
            return sorted(e.get("highlights", []), key=lambda x: x.get("start_line",0))
        return []

    def _panel_navigate(self, delta: int):
        items = self._panel_items()
        if not items: return
        self._panel_cursor = max(0, min(len(items)-1, self._panel_cursor + delta))
        item = items[self._panel_cursor]
        target = item.get("line", item.get("start_line", 0))
        self.current_line = max(0, min(len(self.document.lines)-1, target))
        self.update()

    def _panel_select(self):
        self._panel_mode = None
        self.update()

    def _panel_back(self):
        self._panel_mode = None
        self.current_line = self._pre_panel_line
        self.update()

    def _paint_confirm(self, painter):
        w, h = 460, 90
        x = (self.width()-w)//2
        y = (self.height()-h)//2
        painter.fillRect(QRect(x, y, w, h), QColor(0,0,0,245))
        painter.setPen(AMBER_DARK)
        painter.drawRect(QRect(x, y, w, h))
        painter.setFont(_ui_font(11))
        painter.setPen(AMBER)
        painter.drawText(QRect(x, y, w, h*6//10), Qt.AlignmentFlag.AlignCenter, self._pending["msg"])
        painter.setPen(AMBER_DIM)
        painter.setFont(_ui_font(10))
        painter.drawText(QRect(x, y+h*6//10, w, h*4//10), Qt.AlignmentFlag.AlignCenter, "[y] confirm  [n / Esc] cancel")

    HELP_SECTIONS = [
        ("SCROLLREADER", None, [
            ("", "A focused PDF reader with line-by-line navigation, annotations, and export."),
            ("", "Press Esc to close this panel. Scroll or use Up/Down/PgUp/PgDn to navigate."),
        ]),
        ("READING CONTROLS", None, [
            ("Space  /  Down",        "Advance one line"),
            ("Up  /  Backspace  /  Tab", "Go back one line"),
            ("Scroll wheel",           "Navigate lines (one notch = one line)"),
            ("Page Down",              "Jump one screenful forward"),
            ("Page Up",                "Jump one screenful back"),
            ("Enter",                  "Open command mode"),
            ("Escape",                 "Close panel, cancel confirmation, or exit command mode"),
        ]),
        ("COMMAND MODE", None, [
            ("", "Press Enter to open the command bar (shows ':' prompt). Type a command and"),
            ("", "press Enter to run it, or Escape to cancel. Commands are case-insensitive."),
        ]),
        ("OPENING FILES", None, [
            ("open <path>",   "Open a PDF file. Supports ~ expansion."),
            ("",              "Example:  open ~/books/mybook.pdf"),
        ]),
        ("KEYS — READING", None, [
            ("Space / ↓ / S / Enter",   "Advance one line"),
            ("↑ / Tab / Backspace / W", "Back one line"),
            ("→ / D / PageDown",        "Page forward"),
            ("← / A / PageUp",          "Page back"),
            ("gg",                      "Jump to top of document"),
            ("G  (Shift+G)",            "Jump to bottom of document"),
            ("=",                       "Undo last move (50-step history)"),
            ("F11",                     "Toggle fullscreen"),
            ("Ctrl+Space / Ctrl+Enter", "Open command bar"),
        ]),
        ("KEYS — MODE TOGGLES  (work everywhere, not in command bar)", None, [
            ("L",   "Toggle library  (L again to close)"),
            ("N",   "Toggle notes panel  (N again to close)"),
            ("B",   "Toggle bookmarks panel"),
            ("H",   "Toggle highlights panel"),
            ("I",   "Toggle PDF colour inversion  (per-book, remembered)"),
            ("?",   "This help panel"),
        ]),
        ("KEYS — CTRL SHORTCUTS", None, [
            ("Ctrl+K / Ctrl+L",   "Cycle colour swatch backward / forward"),
            ("Ctrl+O / Ctrl+P",   "Cycle indicator colour through HSV wheel"),
            ("Ctrl+[ / Ctrl+]",   "Highlight band height ±2px"),
            ("Ctrl+= / Ctrl+-",   "UI border width ±1px"),
            ("Ctrl+; / Ctrl+'",   "UI font size ±1"),
            ("Ctrl+, / Ctrl+.",   "Cycle font backward / forward"),
            ("Ctrl+E / Ctrl+R",   "Help panel column offset ±20px"),
            ("Ctrl+F",            "Flip library sizing mode"),
        ]),
        ("NAVIGATION COMMANDS", None, [
            ("gl<N>  /  gotoline<N>",    "Jump to line N"),
            ("gp<N>  /  gotopage<N>",    "Jump to page N"),
            ("lb[N]  /  lineback[N]",    "Go back N lines (default 1)"),
            ("lf[N]  /  lineforward[N]", "Go forward N lines (default 1)"),
            ("pb[N]  /  pageback[N]",    "Go back N pages (default 1)"),
            ("pf[N]  /  pageforward[N]", "Go forward N pages (default 1)"),
        ]),
        ("SEARCH", None, [
            ("sn <term>",   "Search next occurrence from current line"),
            ("sp <term>",   "Search previous"),
            ("sf <term>",   "Search first in document"),
            ("sl <term>",   "Search last in document"),
            ("cc",          "Repeat last command (great for stepping through matches)"),
            ("",            ""),
            ("", "Use ;;phrase;; for multi-word search:  sn ;;eternal return;;"),
            ("", "Results show [wrapped] when the search loops around the document."),
        ]),
        ("RANGE SYNTAX", None, [
            ("", "Many commands accept a range specifier:"),
            ("", ""),
            ("<cmd>",               "Current line or page"),
            ("<cmd><N>",            "Line/page N  (absolute)"),
            ("<cmd><A>-<B>",        "Lines/pages A through B"),
            ("<cmd><fwd>[;<back>]", "fwd forward, back backward from current"),
            ("", ""),
            ("hl5",       "→  highlight current + next 5 lines"),
            ("hl5;3",     "→  highlight back 3, current, forward 5"),
            ("hl40-89",   "→  highlight lines 40–89  (absolute)"),
        ]),
        ("ANNOTATION COMMANDS", None, [
            ("nl[N][;;note;;]",     "Add note at current line or line N"),
            ("bl[range][;;note;;]", "Bookmark a line"),
            ("hl[range][;;note;;]", "Highlight lines"),
            ("vn / vb / vh",        "Open notes / bookmarks / highlights panel"),
            ("",                    ""),
            ("", "In the panel: ↑↓ navigate, Space/Enter select, Tab/Esc back to reading position"),
        ]),
        ("REMOVE COMMANDS", None, [
            ("", "All remove commands show a y/n confirmation. Optional ;;reason;; logged to history."),
            ("", ""),
            ("rl[range]",   "Remove all annotations touching a line range"),
            ("rp[range]",   "Remove all annotations touching a page range"),
            ("rb[range]",   "Remove bookmarks only"),
            ("rn[range]",   "Remove notes only"),
            ("rh[range]",   "Remove highlights only"),
            ("removeall",   "Remove ALL annotations for this book"),
            ("removeall+",  "Wipe ALL stored data for this book"),
        ]),
        ("EXPORT COMMANDS", None, [
            ("", "Two modes:  timestamped (new file per export)  or  running (appends to one file)"),
            ("", "Set with:  set export_mode timestamped  or  set export_mode running"),
            ("", ""),
            ("e",           "Export all annotations"),
            ("el[range]",   "Export by line range"),
            ("ep[range]",   "Export by page range"),
            ("eb[range]",   "Export bookmarks only"),
            ("en[range]",   "Export notes only"),
            ("eh[range]",   "Export highlights only"),
        ]),
        ("LIBRARY", None, [
            ("lib",                         "Open library browser"),
            ("fliplib",                     "Toggle sizing: pages remaining ↔ pages read"),
            ("set library_dir <path>",      "Set library scan folder"),
            ("set library_recursive true",  "Scan subdirectories"),
            ("", ""),
            ("", "Library block size = proportional to unread pages (or pages read in flip mode)."),
            ("", "Overflow cell shows: +12/8U2R1D1A  (U=unread R=reading D=done A=abandoned)"),
            ("", "Navigate: W/S or ↑/↓ move cursor, A/D or ←/→ cycle tabs, Space/Enter opens book"),
        ]),
        ("ZOOM", None, [
            ("zoom fit-width",   "Fit page width to window  (default)"),
            ("zoom fit-page",    "Fit full page height to window"),
            ("zoom 50%",         "Small fixed zoom"),
            ("zoom 75%",         "Medium fixed zoom"),
            ("zoom 100%",        "Large fixed zoom"),
            ("zoom cycle",       "Step through zoom modes"),
        ]),
        ("BOOK METADATA", None, [
            ("bookinfo",                     "Show title, author, status, progress, annotation counts"),
            ("setm title <value>",           "Set book title  (alias: setmeta)"),
            ("setm author <value>",          "Set author"),
            ("setm status <value>",          "Status: unread / reading / read / abandoned"),
            ("setm rating <1-5>",            "Set rating"),
            ("fav  /  unfav",                "Add/remove from favourites"),
        ]),
        ("COLOUR & DISPLAY", None, [
            ("Ctrl+K / Ctrl+L",              "Cycle swatch backward / forward"),
            ("Ctrl+O / Ctrl+P",              "Cycle indicator colour through HSV wheel"),
            ("ms  /  swapmargin",            "Swap annotation margin side (left ↔ right)"),
            ("set theme_primary #ffbb33",    "Set primary UI colour"),
            ("set theme_bg #000000",         "Set background colour"),
            ("set indicator_color #ffb000",  "Set main line indicator colour"),
            ("set preload_inverted true",     "Pre-render inverted page cache (default true)"),
        ]),
        ("GLOBAL CONFIG", None, [
            ("set <key> <value>",            "Change a global config value"),
            ("showconfig",                   "Print all current config values"),
            ("", ""),
            ("set reopen_last true/false",   "Auto-reopen last book on launch"),
            ("set midpoint 0.42",            "Indicator lock position (0.0–1.0)"),
            ("set page_gap 30",              "Pixel gap between PDF pages"),
            ("set export_dir ~/exports",     "Default export directory"),
            ("set help_col_offset 0",        "Help panel column offset (adjust with Ctrl+E/R)"),
        ]),
        ("FILES", None, [
            ("~/.scrollreader/config.json",  "Global configuration"),
            ("~/.scrollreader/history.json", "Per-book history, annotations, and metadata"),
            ("fonts/",                       "Drop .ttf/.otf/.otb here to add fonts"),
            ("", "All data files are plain JSON — safe to edit in any text editor."),
        ]),
        ("PRINT", None, [
            ("pd  /  printdialog",           "Open system print dialog"),
            ("pp[range]",                    "Print pages (same range syntax as everything else)"),
            ("", "Requires PyQt6.QtPrintSupport."),
        ]),
        ("AI COMMANDS", None, [
            ("ebl[N]",   "Extrapolate bookmarks — find relationships between marked passages (N lines context)"),
            ("ebp[N]",   "Extrapolate bookmarks with N pages context"),
            ("ehl[N]",   "Extrapolate highlights with N lines context"),
            ("ehp[N]",   "Extrapolate highlights with N pages context"),
            ("ccl[N]",   "Cultural context for selected bookmarks (N lines)"),
            ("ccp[N]",   "Cultural context for selected bookmarks (N pages)"),
            ("<cmd>0",   "Reuse last selection"),
            ("",         ""),
            ("", "In selection mode: ↑/↓ move, Space toggle, F finish, Esc cancel."),
            ("", "Requires ai_api_key (Anthropic/OpenAI) and ai_model set."),
        ]),
        ("OTHER", None, [
            ("help  /  man  /  ?",  "Show this help panel"),
            ("q  /  quit  /  exit", "Quit ScrollReader"),
        ]),
    ]

    def _paint_help_panel(self, painter: QPainter):
        pw = self.width()
        py = TOP_BAR_H
        ph = self.height() - TOP_BAR_H

        painter.fillRect(QRect(0, py, pw, ph), QColor(0,0,0,252))
        painter.setClipRect(QRect(0, py, pw, ph - 24))

        margin   = 48
        col2_x   = 280
        y        = py + 20 - self._panel_scroll
        lh_body  = 19
        lh_head  = 26

        mono     = _ui_font(10)
        mono_b   = _ui_font(10, bold=True)
        sans     = _ui_font(11, bold=True)

        for section, _, rows in self.HELP_SECTIONS:
            y += 8
            painter.fillRect(QRect(0, y - 2, pw, lh_head), QColor(16,16,16))
            painter.setPen(AMBER_BRIGHT)
            painter.setFont(sans)
            painter.drawText(margin, y + lh_head - 8, section)
            y += lh_head + 4

            for key, val in rows:
                if key == "" and val == "":
                    y += 8; continue

                if key == "":
                    painter.setPen(AMBER_DIM)
                    painter.setFont(mono)
                    painter.drawText(margin, y + lh_body - 4, val)
                    y += lh_body
                else:
                    painter.setPen(AMBER)
                    painter.setFont(mono_b)
                    painter.drawText(margin, y + lh_body - 4, key)
                    if val:
                        painter.setPen(AMBER_DIM)
                        painter.setFont(mono)
                        painter.drawText(col2_x + margin, y + lh_body - 4, val)
                    y += lh_body

            y += 6

        painter.setClipping(False)

        painter.fillRect(QRect(0, self.height()-24, pw, 24), UI_BG)
        painter.setPen(AMBER_VERY_DIM)
        painter.drawLine(0, self.height()-24, pw, self.height()-24)
        painter.setPen(AMBER_DARK)
        painter.setFont(_ui_font(9))
        painter.drawText(margin, self.height()-8, "↑ ↓  PgUp  PgDn  scroll  —  Esc to close")

    # --------------------------------------------------------------- input

    def keyPressEvent(self, ev: QKeyEvent):
        k    = ev.key()
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)

        # Wizard overlay
        if self._wizard and not self._wizard.done:
            wz = self._wizard
            typ = wz.current()[2]
            if k == Qt.Key.Key_Escape or k == Qt.Key.Key_Tab:
                self._close_wizard()
            elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                wz.advance()
                if wz.done: self._close_wizard()
                else: self.update()
            elif k in (Qt.Key.Key_Left, Qt.Key.Key_A) and typ not in ("text","path"):
                wz.adjust(-1)
            elif k in (Qt.Key.Key_Right, Qt.Key.Key_D) and typ not in ("text","path"):
                wz.adjust(1)
            elif k in (Qt.Key.Key_Up, Qt.Key.Key_W):
                wz.back(); self.update()
            elif k in (Qt.Key.Key_Down, Qt.Key.Key_S):
                wz.advance()
                if wz.done: self._close_wizard()
                else: self.update()
            return

        # AI result panel
        if self._ai_result_panel and self._ai_result_panel.get('visible'):
            if k == Qt.Key.Key_Escape:
                self._ai_result_panel = None
                self.update()
                return
            if k in (Qt.Key.Key_Up, Qt.Key.Key_W):
                self._ai_result_scroll(-20)
                return
            if k in (Qt.Key.Key_Down, Qt.Key.Key_S):
                self._ai_result_scroll(20)
                return
            return

        # AI selection mode
        if self._ai_select_mode:
            mode = self._ai_select_mode
            items = mode['items']
            cursor = mode.get('cursor', 0)
            if k in (Qt.Key.Key_Up, Qt.Key.Key_W):
                cursor = max(0, cursor-1)
                mode['cursor'] = cursor
                self.update()
                return
            if k in (Qt.Key.Key_Down, Qt.Key.Key_S):
                cursor = min(len(items)-1, cursor+1)
                mode['cursor'] = cursor
                self.update()
                return
            if k in (Qt.Key.Key_Space, Qt.Key.Key_Return):
                selected = mode.get('selected', set())
                if cursor in selected:
                    selected.discard(cursor)
                else:
                    selected.add(cursor)
                mode['selected'] = selected
                self.update()
                return
            if k == Qt.Key.Key_Escape:
                self._ai_select_mode = None
                self.update()
                return
            if k == Qt.Key.Key_F:
                self._send_ai_request()
                return
            return

        # Translation word selection mode
        if self._translate_mode and not self._translate_fetching:
            if self._translate_result:
                self._exit_translate_mode(); return
            words = self._translate_words()
            n     = len(words)
            if k == Qt.Key.Key_Escape:
                self._exit_translate_mode(); return
            elif k in (Qt.Key.Key_Left, Qt.Key.Key_A):
                self._translate_word_idx = max(0, self._translate_word_idx - 1)
                self.update(); return
            elif k in (Qt.Key.Key_Right, Qt.Key.Key_D):
                self._translate_word_idx = min(n - 1, self._translate_word_idx + 1)
                self.update(); return
            elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                if words:
                    self._do_translate(words[self._translate_word_idx])
                return
            return

        if self._translate_result and not self._translate_fetching:
            self._translate_result = ""
            self.update()

        # Search panel
        if getattr(self, '_search_panel_active', False):
            input_active = getattr(self, '_search_input_active', False)

            if input_active:
                if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._search_input_active = False
                    self.update(); return
                if k == Qt.Key.Key_Escape:
                    self._search_input_active = False
                    self.update(); return
                if k == Qt.Key.Key_Backspace:
                    self._search_query = getattr(self, '_search_query', "")[:-1]
                    self._run_search(self._search_query)
                    self.update(); return
                ch = ev.text()
                if ch and ch.isprintable():
                    self._search_query = getattr(self, '_search_query', "") + ch
                    self._run_search(self._search_query)
                    self.update(); return
                return

            if not ctrl:
                if k == Qt.Key.Key_R:
                    self._search_panel_active = False
                    self.current_line = self._search_pre_line
                    self.update(); return
                if k == Qt.Key.Key_L:
                    self._search_panel_active = False
                    self.window().show_library(); return
                if k == Qt.Key.Key_N:
                    self._search_panel_active = False
                    self._open_annot_panel("notes"); return
                if k == Qt.Key.Key_B:
                    self._search_panel_active = False
                    self._open_annot_panel("bookmarks"); return
                if k == Qt.Key.Key_H:
                    self._search_panel_active = False
                    self._open_annot_panel("highlights"); return
                if k in (Qt.Key.Key_J, Qt.Key.Key_Escape, Qt.Key.Key_Tab):
                    self._search_panel_active = False
                    self.current_line = self._search_pre_line
                    self.update(); return
                if k in (Qt.Key.Key_Up, Qt.Key.Key_W):
                    self._search_cursor = max(0, self._search_cursor - 1)
                    self.update(); return
                if k in (Qt.Key.Key_Down, Qt.Key.Key_S):
                    items = getattr(self, '_search_results', [])
                    self._search_cursor = min(len(items)+1, self._search_cursor + 1)
                    self.update(); return
                if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    self._search_select(); return
            if ctrl and k in (Qt.Key.Key_Space, Qt.Key.Key_Return):
                self._search_input_active = True
                self.update(); return
            return

        # Config popup
        if getattr(self, '_config_popup_open', False):
            settings = self._config_popup_settings()
            if k in (Qt.Key.Key_Escape, Qt.Key.Key_Tab):
                self._config_popup_open = False; self.update()
            elif k in (Qt.Key.Key_Up, Qt.Key.Key_W):
                self._config_popup_idx = (self._config_popup_idx - 1) % max(1, len(settings))
                self.update()
            elif k in (Qt.Key.Key_Down, Qt.Key.Key_S):
                self._config_popup_idx = (self._config_popup_idx + 1) % max(1, len(settings))
                self.update()
            elif k in (Qt.Key.Key_Right, Qt.Key.Key_D):
                self._config_popup_adjust(1)
            elif k in (Qt.Key.Key_Left, Qt.Key.Key_A):
                self._config_popup_adjust(-1)
            return

        # Confirmation overlay
        if self._pending:
            if k == Qt.Key.Key_Y:
                result = self._pending["action"]()
                self._pending = None; self.update()
            elif k in (Qt.Key.Key_N, Qt.Key.Key_Escape, Qt.Key.Key_Tab):
                self._pending = None; self.update()
            return

        # Annotation panel navigation
        if self._panel_mode:
            if not ctrl:
                if k == Qt.Key.Key_R:
                    self._panel_back(); return
                if k == Qt.Key.Key_L:
                    self._panel_back()
                    self.window().show_library(); return
                if k == Qt.Key.Key_N:
                    self._open_annot_panel("notes"); return
                if k == Qt.Key.Key_B:
                    self._open_annot_panel("bookmarks"); return
                if k == Qt.Key.Key_H:
                    self._open_annot_panel("highlights"); return
                if k == Qt.Key.Key_I:
                    _pdf_invert_ref[0] = not _pdf_invert_ref[0]
                    if self.document:
                        self.history._entry(self.document.filepath)["pdf_invert"] = _pdf_invert_ref[0]
                        self.history._save()
                    self.update(); return
            if k in (Qt.Key.Key_Tab, Qt.Key.Key_Escape):
                self._panel_back()
            elif k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._panel_select()
            elif k in (Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_S, Qt.Key.Key_D):
                self._panel_navigate(1)
            elif k in (Qt.Key.Key_Up, Qt.Key.Key_Left, Qt.Key.Key_W, Qt.Key.Key_A):
                self._panel_navigate(-1)
            if ctrl and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if time.time() > self._cmd_cooldown:
                    self._enter_command_mode()
            return

        # Help/overlay panel
        if self.panel:
            if not ctrl and not self.command_mode:
                if k == Qt.Key.Key_R:
                    self.panel = None; self._panel_scroll = 0; self.update(); return
                if k == Qt.Key.Key_L:
                    self.panel = None; self._panel_scroll = 0
                    self.window().show_library(); return
                if k == Qt.Key.Key_N:
                    self.panel = None; self._panel_scroll = 0
                    self._open_annot_panel("notes"); return
                if k == Qt.Key.Key_B:
                    self.panel = None; self._panel_scroll = 0
                    self._open_annot_panel("bookmarks"); return
                if k == Qt.Key.Key_H:
                    self.panel = None; self._panel_scroll = 0
                    self._open_annot_panel("highlights"); return
                if k == Qt.Key.Key_J:
                    self.panel = None; self._panel_scroll = 0
                    self._open_search_panel(); return
            if k in (Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Question, Qt.Key.Key_Slash):
                self.panel = None; self._panel_scroll = 0; self.update()
            elif k in (Qt.Key.Key_Down, Qt.Key.Key_Space, Qt.Key.Key_S):
                self._panel_scroll += 40; self.update()
            elif k in (Qt.Key.Key_Up, Qt.Key.Key_W):
                self._panel_scroll = max(0, self._panel_scroll - 40); self.update()
            elif k == Qt.Key.Key_PageDown:
                self._panel_scroll += self.height() - TOP_BAR_H - 60; self.update()
            elif k == Qt.Key.Key_PageUp:
                self._panel_scroll = max(0, self._panel_scroll - (self.height() - TOP_BAR_H - 60)); self.update()
            return

        if self.command_mode:
            if k == Qt.Key.Key_Escape: self._exit_command_mode()
            return

        # Ctrl shortcuts
        if ctrl:
            if k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if getattr(self, '_search_panel_active', False):
                    self._search_input_active = True
                    self.update(); return
                self._enter_command_mode(); return

            if k == Qt.Key.Key_T:
                self._adj_dim("top_bar_h", -2, 16, 60); return
            if k == Qt.Key.Key_Y:
                self._adj_dim("top_bar_h", 2, 16, 60); return
            if k == Qt.Key.Key_G:
                self._adj_dim("panel_w", -5, 80, 400); return
            if k == Qt.Key.Key_H:
                self._adj_dim("panel_w", 5, 80, 400); return
            if k == Qt.Key.Key_B:
                self._adj_dim("bottom_bar_h", -2, 16, 60); return
            if k == Qt.Key.Key_N:
                self._adj_dim("bottom_bar_h", 2, 16, 60); return
            if k == Qt.Key.Key_Comma:
                cur = int(self.config.get("page_gap") or 30)
                self.config.set("page_gap", max(0, cur - 5))
                self.status_text = f"page gap: {self.config.get('page_gap')}px"
                QTimer.singleShot(3000, self._clear_status)
                self.update(); return
            if k == Qt.Key.Key_Period:
                cur = int(self.config.get("page_gap") or 30)
                self.config.set("page_gap", cur + 5)
                self.status_text = f"page gap: {self.config.get('page_gap')}px"
                QTimer.singleShot(3000, self._clear_status)
                self.update(); return

            if k == Qt.Key.Key_E:
                self._cycle_font(-1); return
            if k == Qt.Key.Key_R:
                self._cycle_font(1); return
            if k == Qt.Key.Key_D:
                _UI_FONT_OFFSET_ref[0] = max(-6, _UI_FONT_OFFSET_ref[0] - 1)
                self.config.set("ui_font_offset", str(_UI_FONT_OFFSET_ref[0]))
                self._update_cmd_style(); self.update(); return
            if k == Qt.Key.Key_F:
                _UI_FONT_OFFSET_ref[0] += 1
                self.config.set("ui_font_offset", str(_UI_FONT_OFFSET_ref[0]))
                self._update_cmd_style(); self.update(); return
            if k == Qt.Key.Key_J:
                cur = int(self.config.get("ui_border_width") or 2)
                self.config.set("ui_border_width", str(max(1, cur - 1)))
                self.update(); return
            if k == Qt.Key.Key_K:
                cur = int(self.config.get("ui_border_width") or 2)
                self.config.set("ui_border_width", str(cur + 1))
                self.update(); return

            if k == Qt.Key.Key_BracketLeft:
                cur = int(self._cfg("highlight_height") or 20)
                self.config.set("highlight_height", str(max(4, cur - 2)))
                self.update(); return
            if k == Qt.Key.Key_BracketRight:
                cur = int(self._cfg("highlight_height") or 20)
                self.config.set("highlight_height", str(cur + 2))
                self.update(); return
            if k == Qt.Key.Key_Semicolon:
                self._cycle_indicator_color(-5, key="highlight_color"); return
            if k == Qt.Key.Key_Apostrophe:
                self._cycle_indicator_color(5,  key="highlight_color"); return
            if k == Qt.Key.Key_L:
                cur = int(self._cfg("highlight_alpha") or 35)
                self.config.set("highlight_alpha", str(max(0, min(255, cur + 10))))
                self.update(); return

            if k == Qt.Key.Key_M:
                self._cycle_swatch(1); return
            if k == Qt.Key.Key_O:
                self._cycle_indicator_color(-5); return
            if k == Qt.Key.Key_P:
                self._cycle_indicator_color(5); return

            if k == Qt.Key.Key_U:
                cur = float(self.config.get("midpoint") or 0.42)
                self.config.set("midpoint", round(max(0.1, cur - 0.02), 3))
                self.status_text = f"midpoint: {self.config.get('midpoint')}"
                QTimer.singleShot(3000, self._clear_status)
                self.update(); return
            if k == Qt.Key.Key_I:
                cur = float(self.config.get("midpoint") or 0.42)
                self.config.set("midpoint", round(min(0.9, cur + 0.02), 3))
                self.status_text = f"midpoint: {self.config.get('midpoint')}"
                QTimer.singleShot(3000, self._clear_status)
                self.update(); return

            if k == Qt.Key.Key_Slash:
                self._open_panel("ScrollReader — Controls Reference", "help")
                return
            return

        # F11 fullscreen
        if k == Qt.Key.Key_F11:
            w = self.window()
            if w.isFullScreen(): w.showMaximized()
            else:                w.showFullScreen()
            return

        # Normal reading keys
        if k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down, Qt.Key.Key_S):
            self._step(1)
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Backspace, Qt.Key.Key_Tab, Qt.Key.Key_W):
            self._step(-1)
        elif k in (Qt.Key.Key_PageDown, Qt.Key.Key_Right, Qt.Key.Key_D):
            self._step(self._lines_per_screen())
        elif k in (Qt.Key.Key_PageUp, Qt.Key.Key_Left, Qt.Key.Key_A):
            self._step(-self._lines_per_screen())
        elif k == Qt.Key.Key_I:
            _pdf_invert_ref[0] = not _pdf_invert_ref[0]
            if self.document:
                self.history._entry(self.document.filepath)["pdf_invert"] = _pdf_invert_ref[0]
                self.history._save()
                if _pdf_invert_ref[0]:
                    cur_page = self.document.lines[self.current_line].page_num if self.document.lines else 0
                    eager    = int(self.config.get("eager_pages") or 2)
                    for pn in range(max(0, cur_page-eager), min(self.document.page_count, cur_page+eager+1)):
                        if self.document.page_pixmaps_inv[pn] is None:
                            self.document.page_pixmaps_inv[pn] = self.document.render_page_inv(pn)
            self.update()
        elif k == Qt.Key.Key_Equal:
            prev = self._pop_history()
            if prev is not None and self.document:
                self.current_line = max(0, min(len(self.document.lines)-1, prev))
                self.update()
        elif k == Qt.Key.Key_J:
            self._open_search_panel()
        elif k in (Qt.Key.Key_Question, Qt.Key.Key_Slash):
            self._open_panel("ScrollReader — Command Reference", "help")
        elif k == Qt.Key.Key_G:
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if self.document and self.document.lines:
                    self._push_history(self.current_line)
                    self.current_line = len(self.document.lines) - 1
                    self.history.set_line(self.document.filepath, self.current_line)
                    self.update()
            else:
                if getattr(self, '_g_pending', False):
                    self._g_pending = False
                    if self.document and self.document.lines:
                        self._push_history(self.current_line)
                        self.current_line = 0
                        self.history.set_line(self.document.filepath, self.current_line)
                        self.update()
                else:
                    self._g_pending = True
                    QTimer.singleShot(500, lambda: setattr(self, '_g_pending', False))
        elif k == Qt.Key.Key_L:
            self.window().show_library()
        elif k == Qt.Key.Key_N:
            self._open_annot_panel("notes")
        elif k == Qt.Key.Key_B:
            self._open_annot_panel("bookmarks")
        elif k == Qt.Key.Key_H:
            self._open_annot_panel("highlights")
        elif k == Qt.Key.Key_T:
            self._enter_translate_mode()
        elif k == Qt.Key.Key_R:
            if self._panel_mode:
                self._panel_back()

    def focusOutEvent(self, ev):
        self._g_pending = False
        super().focusOutEvent(ev)

    def wheelEvent(self, ev: QWheelEvent):
        delta = ev.angleDelta().y()
        if not delta: return
        if self._ai_result_panel and self._ai_result_panel.get('visible'):
            self._ai_result_scroll(-delta//3)
            return
        if self._ai_select_mode:
            self._ai_select_scroll = max(0, self._ai_select_scroll - delta//3)
            self.update()
            return
        if self._panel_mode:
            self._panel_navigate(-1 if delta < 0 else 1)
            return
        if self.panel:
            self._panel_scroll = max(0, self._panel_scroll - (delta//3))
            self.update()
            return
        if not self._pending:
            self._step(-(delta//120))

    def mousePressEvent(self, ev: QMouseEvent):
        if self.panel and ev.button() == Qt.MouseButton.LeftButton:
            pos = ev.pos()
            for rect, line_idx in self._panel_rects:
                if rect.contains(pos):
                    self.panel = None
                    self.current_line = max(0, min(len(self.document.lines)-1, line_idx))
                    self.history.set_line(self.document.filepath, self.current_line)
                    self.update()
                    return
        super().mousePressEvent(ev)

    def _step(self, delta):
        if not self.document: return
        self._push_history(self.current_line)
        self.current_line = max(0, min(len(self.document.lines)-1, self.current_line+delta))
        self.history.set_line(self.document.filepath, self.current_line)
        self.update()

    def _jump_pages(self, direction):
        if not self.document or not self.document.lines: return
        self._push_history(self.current_line)
        cp = self.document.lines[self.current_line].page_num
        tp = max(0, min(self.document.page_count-1, cp+direction))
        for i, l in enumerate(self.document.lines):
            if l.page_num == tp: self.current_line = i; break
        self.history.set_line(self.document.filepath, self.current_line)
        self.update()

    def _execute_command(self):
        raw = self.cmd.text().lstrip(":").strip()
        self._exit_command_mode()
        if not raw: return
        if not self._cmd_history or self._cmd_history[-1] != raw:
            self._cmd_history.append(raw)
            if len(self._cmd_history) > 100:
                self._cmd_history.pop(0)
        result = self._run(raw)
        if result:
            self.status_text = result
            self.update()
            QTimer.singleShot(5000, lambda: self._clear_status())

    def _cycle_font(self, direction: int):
        fonts = _scan_fonts()
        if not fonts: return
        idx = int(self.config.get("current_font_idx") or 0)
        idx = (idx + direction) % len(fonts)
        fam = _load_font_by_path(fonts[idx])
        _UI_FONT_FAMILY_ref[0] = fam
        self.config.set("current_font_idx", str(idx))
        self._update_cmd_style()
        self.status_text = f"font: {os.path.basename(fonts[idx])}"
        QTimer.singleShot(5000, self._clear_status)
        self.update()

    def _cycle_swatch(self, direction: int = 1):
        cur  = self.config.get("current_swatch") or "amber"
        idx  = SWATCH_NAMES.index(cur) if cur in SWATCH_NAMES else 0
        idx  = (idx + direction) % len(SWATCH_NAMES)
        name = SWATCH_NAMES[idx]
        _apply_swatch(name, self.config)
        _current_swatch_ref[0] = idx
        self._update_cmd_style()
        self.status_text = f"swatch: {name}"
        QTimer.singleShot(5000, self._clear_status)
        self.update()

    def _open_search_panel(self):
        if not self.document: return
        self._search_panel_active = True
        self._search_results      = getattr(self, '_search_results', [])
        self._search_pre_line     = self.current_line
        self._search_cursor       = 0
        self.update()

    def _open_config_popup(self, kind: str):
        self._config_popup_kind  = kind
        self._config_popup_idx   = 0
        self._config_popup_open  = True
        self.update()

    def _config_popup_settings(self):
        if getattr(self, '_config_popup_kind', 'global') == 'global':
            return [
                ("current_swatch",    "Swatch",        "choice",  0, 0, 1,    SWATCH_NAMES),
                ("indicator_color",   "Indicator",     "color",   0, 0, 5,    None),
                ("ui_font_offset",    "Font Size",     "int",    -6, 10, 1,   None),
                ("ui_border_width",   "Border Width",  "int",     1, 10, 1,   None),
                ("midpoint",          "Midpoint",      "float", 0.1, 0.9, 0.02, None),
                ("highlight_height",  "HL Height",     "int",     4, 60, 2,   None),
                ("highlight_alpha",   "HL Alpha",      "int",     0, 255, 5,  None),
                ("zoom_mode",         "Zoom Mode",     "choice",  0, 0, 1,    ["fit-width","fit-page","50%","75%","100%"]),
                ("page_gap",          "Page Gap",      "int",     0, 100, 5,  None),
                ("library_flip_mode", "Lib Flip",      "bool",    0, 1, 1,    None),
                ("preload_inverted",  "Preload Inv",   "bool",    0, 1, 1,    None),
            ]
        else:
            return [
                ("zoom_fixed",           "Zoom",        "float",  0.3, 5.0, 0.1,  None),
                ("margin_side",          "Margin",      "choice", 0, 0, 1, ["right","left"]),
                ("indicator_color",      "Indicator",   "color",  0, 0, 5,   None),
                ("highlight_alpha",      "HL Alpha",    "int",    0, 255, 5, None),
            ]

    def _config_popup_adjust(self, direction: int):
        settings = self._config_popup_settings()
        if not settings: return
        idx = self._config_popup_idx % len(settings)
        key, label, typ, mn, mx, step, choices = settings[idx]
        cur = self.config.get(key)

        if typ == "choice" and choices:
            cur_idx = choices.index(cur) if cur in choices else 0
            new_idx = (cur_idx + direction) % len(choices)
            self.config.set(key, choices[new_idx])
            if key == "current_swatch":
                _apply_swatch(choices[new_idx], self.config)
                self._update_cmd_style()
        elif typ == "bool":
            self.config.set(key, not bool(cur))
        elif typ == "int":
            self.config.set(key, max(int(mn), min(int(mx), int(cur or mn) + direction * int(step))))
        elif typ == "float":
            self.config.set(key, round(max(mn, min(mx, float(cur or mn) + direction * step)), 3))
        elif typ == "color":
            c = QColor(cur or "#ffb000")
            h, s, v, _ = c.getHsvF()
            self.config.set(key, QColor.fromHsvF((h + direction * step/360) % 1.0, s, v).name())
        self.update()

    def _paint_config_popup(self, painter: QPainter):
        settings = self._config_popup_settings()
        if not settings: return
        idx      = getattr(self, '_config_popup_idx', 0) % len(settings)
        w, h     = self.width(), self.height()
        pw       = w // 2
        row_h    = 32
        ph       = row_h * 3 + 8
        px       = w - pw - 4
        py       = h - BOTTOM_BAR_H - ph - 4
        font     = _ui_font(10)
        font_b   = _ui_font(10, bold=True)
        fm       = QFontMetrics(font)

        painter.fillRect(QRect(px, py, pw, ph), UI_BG)
        painter.setPen(_mk_pen(AMBER_DARK, self._bw()))
        painter.drawRect(QRect(px, py, pw, ph))

        rows = []
        for offset in [-1, 0, 1]:
            ridx = (idx + offset) % len(settings)
            rows.append((offset, settings[ridx]))

        for offset, (key, label, typ, mn, mx, step, choices) in rows:
            ry = py + (offset + 1) * row_h + 4
            if offset == 0:
                painter.fillRect(QRect(px+2, ry-2, pw-4, row_h-2), AMBER_VERY_DIM)
                painter.setPen(AMBER_BRIGHT)
                painter.setFont(font_b)
            else:
                painter.setPen(AMBER_DIM)
                painter.setFont(font)
            cur = self.config.get(key)
            txt = f"{label}   {cur}"
            painter.drawText(px+10, ry + row_h//2 + fm.height()//2 - 4, txt)

        painter.setPen(AMBER_VERY_DIM)
        painter.setFont(_ui_font(8))
        painter.drawText(px+10, py+ph-4, "L-stick navigate · R-stick adjust · B to close")

    def _paint_search_panel(self, painter: QPainter, mr: QRect):
        results = getattr(self, '_search_results', [])
        cursor  = getattr(self, '_search_cursor', 0)
        font_b  = _ui_font(9, bold=True)
        font    = _ui_font(9)
        px, pw  = mr.left(), mr.width()
        py      = mr.top() + 4

        painter.fillRect(QRect(px, mr.top(), pw, 4), AMBER_VERY_DIM)

        items = [("PREVIOUS", -1), ("NEXT", 1)] + [(f"L{r+1}", r) for r in results]
        for i, (label, _val) in enumerate(items):
            item_h = 20
            if py + item_h > mr.bottom(): break
            selected = (i == cursor)
            if selected:
                painter.fillRect(QRect(px+2, py, pw-4, item_h), AMBER_INV_BG)
                painter.setPen(AMBER_INV_FG)
            else:
                painter.setPen(AMBER if i < 2 else AMBER_DIM)
            painter.setFont(font_b if i < 2 else font)
            painter.drawText(px+8, py + item_h - 5, label)
            py += item_h

    def _paint_vu_meter(self, painter: QPainter):
        style = self.config.get("progress_bar_style") or "mini"

        render_frac = 0.0
        inv_frac    = 0.0
        rendering   = self._render_thread is not None and self.document is not None
        if rendering:
            render_frac = sum(1 for p in self.document.page_pixmaps if p is not None) / max(self.document.page_count, 1)
            inv_frac    = sum(1 for p in self.document.page_pixmaps_inv if p is not None) / max(self.document.page_count, 1)

        if style == "full" and rendering:
            self._paint_full_bar(painter, render_frac, inv_frac)
            if getattr(self, '_vu_active', False):
                self._paint_audio_vu(painter)
            return

        tasks = []
        if rendering:
            tasks.append(("render", render_frac, AMBER))
            if inv_frac < 1.0:
                tasks.append(("invert", inv_frac, AMBER_DIM))
        if getattr(self, '_vu_active', False):
            level = _audio_recorder.vu_level
            col   = QColor("#ff4444") if level > 0.8 else QColor("#ffbb33") if level > 0.4 else QColor("#44ff88")
            tasks.append(("audio", level, col))

        if not tasks: return

        w, h    = self.width(), self.height()
        bar_w   = 6
        bar_gap = 3
        bar_h   = BOTTOM_BAR_H - 6
        total_w = len(tasks) * (bar_w + bar_gap) - bar_gap
        start_x = w - total_w - 8
        base_y  = h - BOTTOM_BAR_H + 3

        for i, (name, frac, color) in enumerate(tasks):
            bx = start_x + i * (bar_w + bar_gap)
            painter.fillRect(QRect(bx, base_y, bar_w, bar_h), AMBER_VERY_DIM)
            fill_h = max(2, int(bar_h * frac))
            fy     = base_y + bar_h - fill_h
            painter.fillRect(QRect(bx, fy, bar_w, fill_h), QColor(color))

        if getattr(self, '_vu_active', False):
            painter.setPen(QColor("#ff4444"))
            painter.setFont(_ui_font(8))
            painter.drawText(start_x - 30, h - BOTTOM_BAR_H + bar_h - 1, "REC")

    def _paint_full_bar(self, painter: QPainter, render_frac: float, inv_frac: float):
        mr    = self._margin_rect()
        track_x = mr.left() + 2
        track_w = mr.width() - 4
        track_y = mr.top()
        track_h = mr.height()

        ind = QColor(self._cfg("indicator_color") or "#ffb000")

        fill_h = int(track_h * render_frac)
        if fill_h > 0:
            c = QColor(ind); c.setAlpha(80)
            painter.fillRect(QRect(track_x, track_y + track_h - fill_h,
                                   track_w, fill_h), c)

        if inv_frac > 0:
            drain_h = int(track_h * inv_frac)
            painter.fillRect(QRect(track_x, track_y + track_h - fill_h,
                                   track_w, min(drain_h, fill_h)), UI_BG)

    def _paint_audio_vu(self, painter: QPainter):
        level  = _audio_recorder.vu_level
        w, h   = self.width(), self.height()
        bar_w  = 6
        bar_h  = BOTTOM_BAR_H - 6
        bx     = w - bar_w - 8
        base_y = h - BOTTOM_BAR_H + 3
        col    = QColor("#ff4444") if level > 0.8 else QColor("#ffbb33") if level > 0.4 else QColor("#44ff88")
        painter.fillRect(QRect(bx, base_y, bar_w, bar_h), AMBER_VERY_DIM)
        fill_h = max(2, int(bar_h * level))
        painter.fillRect(QRect(bx, base_y + bar_h - fill_h, bar_w, fill_h), col)
        painter.setPen(QColor("#ff4444"))
        painter.setFont(_ui_font(8))
        painter.drawText(bx - 30, h - BOTTOM_BAR_H + bar_h - 1, "REC")

    def _paint_scrollbar(self, painter: QPainter, mr: QRect, scroll: float, vp: QRect):
        if not self.document or not self.document.lines: return
        total_h = self.document.total_height
        if total_h <= 0: return
        vp_h = vp.height()

        track_x = mr.left()
        track_y = mr.top()
        track_w = mr.width()
        track_h = mr.height()

        thumb_h = max(8, int(track_h * vp_h / max(total_h, 1)))
        thumb_y = track_y + int((track_h - thumb_h) * scroll / max(total_h - vp_h, 1))
        thumb_y = max(track_y, min(track_y + track_h - thumb_h, thumb_y))

        c = QColor(AMBER_DIM); c.setAlpha(60)
        painter.fillRect(QRect(track_x, thumb_y, track_w, thumb_h), c)

    def _open_panel(self, title: str, kind: str):
        self.panel = {"title": title, "kind": kind, "items": []}
        self._panel_scroll = 0
        self.update()

    def _open_search_panel(self):
        if not self.document: return
        self._search_panel_active = True
        self._search_input_active = False
        self._search_results      = getattr(self, '_search_results', [])
        self._search_pre_line     = self.current_line
        self._search_cursor       = 0
        self._search_query        = getattr(self, '_search_query', "")
        self.update()

    def _run_search(self, query: str):
        if not self.document or not query: return
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        self._search_results = [
            i for i, ln in enumerate(self.document.lines)
            if pattern.search(ln.text)
        ]
        self._search_cursor = 0

    def _search_select(self):
        results = getattr(self, '_search_results', [])
        cur     = getattr(self, '_search_cursor', 0)
        if cur == 0:
            matches = [r for r in results if r < self.current_line]
            if matches:
                self._push_history(self.current_line)
                self.current_line = matches[-1]
        elif cur == 1:
            matches = [r for r in results if r > self.current_line]
            if matches:
                self._push_history(self.current_line)
                self.current_line = matches[0]
        else:
            idx = cur - 2
            if 0 <= idx < len(results):
                self._push_history(self.current_line)
                self.current_line = results[idx]
        self.update()

    # Translation methods (shortened for space, but functional)

    def _translate_anim_tick(self):
        if self._translate_fetching:
            self._translate_anim_frame += 1
            self.update()

    def _translate_words(self) -> list:
        if not self.document or not self.document.lines: return []
        return self.document.lines[self.current_line].text.split()

    def _enter_translate_mode(self):
        if not self.document or not self.document.lines: return
        self._translate_mode     = True
        self._translate_word_idx = 0
        self._translate_result   = ""
        self._translate_fetching = False
        self._translate_word_xs  = self._build_word_xs()
        self.update()

    def _build_word_xs(self) -> list:
        if not self.document or not self.document.lines:
            return []
        line     = self.document.lines[self.current_line]
        page_num = line.page_num
        zoom     = self.document.zoom
        px       = self._page_x_offset()
        scroll   = self._scroll_offset()
        vp       = self._vp()
        page_y0 = (line.abs_y / zoom) - (self.document.page_offsets[page_num] / zoom)
        try:
            page_words = self.document.doc[page_num].get_text("words")
        except Exception:
            return []
        tol = (self._lh() / zoom) * 0.75
        line_words = [w for w in page_words if abs(w[1] - page_y0) < tol]
        line_words.sort(key=lambda w: w[0])
        if not line_words:
            return []
        xs = [px + int(w[0] * zoom) for w in line_words]
        self._translate_pdf_words = [w[4] for w in line_words]
        return xs

    def _translate_word_x(self, word_idx: int) -> int:
        xs = getattr(self, '_translate_word_xs', [])
        if xs and word_idx < len(xs):
            return xs[word_idx]
        if not self.document or not self.document.lines:
            return self.width() // 2
        words  = self._translate_words()
        px     = self._page_x_offset()
        char_w = max(4, int(7 * self.document.zoom))
        prefix = " ".join(words[:word_idx])
        return px + len(prefix) * char_w + (char_w if word_idx > 0 else 0)

    def _exit_translate_mode(self):
        self._translate_mode     = False
        self._translate_fetching = False
        self._translate_result   = ""
        self._translate_word_xs  = []
        if self._translate_thread:
            self._translate_thread = None
        self.update()

    def _do_translate(self, text: str):
        lang = self.config.get("translate_target_lang") or ""
        if not lang:
            self._translate_result   = "set language: set translate_target_lang <code>"
            self._translate_fetching = False
            self.update()
            return
        self._translate_result     = ""
        self._translate_fetching   = True
        self._translate_anim_frame = 0
        t = TranslateThread(text, lang, self.config)
        t.result_ready.connect(self._on_translate_result)
        self._translate_thread = t
        t.start()
        self.update()

    def _on_translate_result(self, text: str):
        self._translate_result   = text
        self._translate_fetching = False
        self._translate_thread   = None
        self.update()

    def _translate_line(self):
        if not self.document or not self.document.lines: return
        self._translate_mode   = False
        self._translate_result = ""
        self._do_translate(self.document.lines[self.current_line].text)

    def _paint_translate_overlay(self, painter: QPainter):
        ANIM = [' >>>', '> >>', '>> >', '>>> ']
        font   = _ui_font(10, bold=True)
        fm     = QFontMetrics(font)
        vp     = self._vp()
        ind_y  = self._indicator_screen_y()
        box_h  = 36

        if self._translate_fetching:
            arrow = ANIM[self._translate_anim_frame % len(ANIM)]
            disp  = f"{arrow} translating..."
        elif self._translate_result:
            disp  = self._translate_result
        elif self._translate_mode:
            words = self._translate_words()
            if words:
                disp = f"[ {words[self._translate_word_idx]} ]  ←/→ select  Enter confirm  Esc cancel"
            else:
                disp = "(empty line)"
        else:
            return

        pw  = min(fm.horizontalAdvance(disp) + 40, vp.width() - 20)
        box_y = max(vp.top() + 4, ind_y - box_h - 8)

        if self._translate_mode:
            raw_x = self._translate_word_x(self._translate_word_idx)
            px    = max(vp.left() + 4, min(vp.right() - pw - 4, raw_x))
        else:
            px = vp.left() + (vp.width() - pw) // 2

        painter.fillRect(QRect(px, box_y, pw, box_h), UI_BG)
        painter.setPen(_mk_pen(AMBER_BRIGHT, self._bw()))
        painter.drawRect(QRect(px, box_y, pw, box_h))
        painter.setPen(AMBER_BRIGHT)
        painter.setFont(font)
        painter.drawText(QRect(px + 10, box_y, pw - 14, box_h),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, disp)

        if self._translate_mode and not self._translate_fetching and not self._translate_result:
            words = self._translate_words()
            if words:
                idx  = self._translate_word_idx
                wx   = self._translate_word_x(idx)
                word = words[idx]
                xs   = getattr(self, '_translate_word_xs', [])
                if xs and idx < len(xs) - 1:
                    ww = xs[idx + 1] - xs[idx] - 2
                elif xs and idx == len(xs) - 1:
                    ww = len(word) * max(4, int(7 * self.document.zoom))
                else:
                    ww = len(word) * max(4, int(7 * self.document.zoom))
                lh   = self._lh()
                ind  = QColor(self._cfg("indicator_color") or "#ffb000")
                h, s, v, _ = ind.getHsvF()
                comp = QColor.fromHsvF((h + 0.5) % 1.0, max(0.6, s), max(0.7, v))
                comp.setAlpha(120)
                painter.fillRect(QRect(wx, ind_y, ww, lh), comp)

    # Wizard methods

    def _middle_line(self) -> int:
        if not self.document or not self.document.lines: return 0
        mid_page  = self.document.page_count // 2
        for delta in range(self.document.page_count):
            for pn in [mid_page + delta, mid_page - delta]:
                if 0 <= pn < self.document.page_count:
                    page_lines = [i for i, l in enumerate(self.document.lines) if l.page_num == pn]
                    if page_lines:
                        return page_lines[len(page_lines) // 2]
        return 0

    def _open_wizard(self, kind: str):
        self._wizard = WizardOverlay(kind, self)
        if kind == "book" and self.document:
            self._wizard_pre_line   = self.current_line
            self._wizard_active     = True
            self.current_line       = self._middle_line()
        self.update()

    def _close_wizard(self):
        wz = self._wizard
        self._wizard = None
        if wz and wz.kind == "app":
            self.config.set("wizard_completed", True)
        if getattr(self, '_wizard_active', False):
            self._wizard_active = False
            self.current_line   = getattr(self, '_wizard_pre_line', 0)
            if self.document:
                e = self.history._entry(self.document.filepath)
                if e.get("line", 0) == 0:
                    e["status"] = "unread"
                    self.history._save()
        self.update()

    # AI selection and result methods

    def _enter_ai_selection(self, selection_type: str, context: int, unit: str, cmd: str):
        if not self.document:
            self.status_text = "no document open"
            return
        e = self.history._entry(self.document.filepath)
        if selection_type == 'bookmarks':
            items = e.get('bookmarks', [])
        elif selection_type == 'highlights':
            items = e.get('highlights', [])
        else:
            return
        if not items:
            self.status_text = f"no {selection_type} to select"
            return
        self._ai_select_mode = {
            'cmd': cmd,
            'type': selection_type,
            'context': context,
            'unit': unit,
            'items': items,
            'selected': set(),
            'cursor': 0,
        }
        self._ai_select_scroll = 0
        self.update()

    def _paint_ai_selection(self, painter: QPainter, mr: QRect):
        mode = self._ai_select_mode
        if not mode: return
        items = mode['items']
        selected = mode['selected']
        cursor = mode.get('cursor', 0)
        scroll = self._ai_select_scroll

        painter.fillRect(mr, UI_BG)
        painter.setPen(AMBER_BRIGHT)
        painter.setFont(_ui_font(9, bold=True))
        header = f"SELECT {mode['type'].upper()}  (F: finish, Esc: cancel)"
        painter.drawText(mr.left()+8, mr.top()+20, header)
        painter.setPen(_mk_pen(AMBER_VERY_DIM, 1))
        painter.drawLine(mr.left(), mr.top()+28, mr.right(), mr.top()+28)

        font = _ui_font(9)
        fm = QFontMetrics(font)
        lh = fm.height() + 4
        y = mr.top() + 36 - scroll
        for idx, item in enumerate(items):
            if y + lh < mr.top(): continue
            if y > mr.bottom(): break

            rect = QRect(mr.left()+4, y, mr.width()-8, lh)
            if idx == cursor:
                painter.fillRect(rect, AMBER_VERY_DIM)
                painter.setPen(AMBER_BRIGHT)
            else:
                painter.setPen(AMBER_DIM)

            chk_rect = QRect(mr.left()+8, y+4, 12, 12)
            if idx in selected:
                painter.fillRect(chk_rect, AMBER_BRIGHT)
                painter.setPen(UI_BG)
                painter.drawText(chk_rect, Qt.AlignmentFlag.AlignCenter, "✓")
            else:
                painter.drawRect(chk_rect)

            if mode['type'] == 'bookmarks':
                line = item.get('line', 0) + 1
                note = item.get('note', '')
                label = f"L{line}"
            else:
                sl = item.get('start_line', 0) + 1
                el = item.get('end_line', 0) + 1
                label = f"L{sl}-{el}"
                note = item.get('note', '')
            text_x = mr.left() + 28
            painter.drawText(text_x, y + lh - 5, label)
            if note and y + lh + 12 < mr.bottom():
                painter.setFont(_ui_font(8))
                truncated = fm.elidedText(note, Qt.TextElideMode.ElideRight, mr.width() - 36)
                painter.drawText(text_x, y + lh + 8, truncated)
                painter.setFont(font)
            y += lh + 4

        painter.setPen(AMBER_DARK)
        painter.setFont(_ui_font(8))
        painter.drawText(mr.left()+8, mr.bottom()-8, "↑/↓ move   Space select   F finish   Esc cancel")

    def _extract_context(self, item: dict, context: int, unit: str) -> str:
        if not self.document: return ""
        lines = self.document.lines
        total = len(lines)
        if unit == 'line':
            if 'line' in item:
                center = item['line']
            elif 'start_line' in item:
                center = (item['start_line'] + item['end_line']) // 2
            else:
                return ""
            start = max(0, center - context)
            end = min(total-1, center + context)
            text_parts = []
            for i in range(start, end+1):
                text_parts.append(f"[L{i+1}] {lines[i].text}")
            return "\n".join(text_parts)
        else:
            if 'line' in item:
                page = lines[item['line']].page_num
            elif 'start_line' in item:
                page = lines[item['start_line']].page_num
            else:
                return ""
            start_page = max(0, page - context)
            end_page = min(self.document.page_count-1, page + context)
            text_parts = []
            for pg in range(start_page, end_page+1):
                page_text = self.document.doc[pg].get_text("text")
                text_parts.append(f"[Page {pg+1}]\n{page_text}")
            return "\n".join(text_parts)

    def _send_ai_request(self):
        mode = self._ai_select_mode
        if not mode: return
        selected_indices = mode['selected']
        items = mode['items']
        if not selected_indices:
            self.status_text = "no items selected"
            return
        context = mode['context']
        unit = mode['unit']
        selection_type = mode['type']
        cmd = mode.get('cmd', 'ebl')

        self._last_ai_selection = {
            'type': selection_type,
            'context': context,
            'unit': unit,
            'items': [items[i] for i in selected_indices]
        }

        passages = []
        for idx in selected_indices:
            item = items[idx]
            text = self._extract_context(item, context, unit)
            if text:
                if selection_type == 'bookmarks':
                    line = item.get('line', 0) + 1
                    note = item.get('note', '')
                    header = f"Bookmark at line {line}" + (f" — note: {note}" if note else "")
                else:
                    sl = item.get('start_line', 0) + 1
                    el = item.get('end_line', 0) + 1
                    note = item.get('note', '')
                    header = f"Highlight lines {sl}-{el}" + (f" — note: {note}" if note else "")
                passages.append(f"{header}\n{text}")

        if not passages:
            self.status_text = "no text extracted"
            return

        if cmd.startswith('ccl') or cmd.startswith('ccp'):
            prompt = ("Given the time and place this book was written, "
                      "what is the cultural context of the following passages? "
                      "Explain briefly.\n\n") + "\n\n---\n\n".join(passages)
        else:
            prompt = ("Find relationships and connections between the following passages "
                      "from this book. Extrapolate meaning, themes, or insights.\n\n") + "\n\n---\n\n".join(passages)

        self._ai_result_panel = {
            'loading': True,
            'result': '',
            'scroll': 0,
            'visible': True
        }
        self.update()

        provider = self.config.get("ai_provider", "anthropic")
        api_key = self.config.get("ai_api_key", "")
        model = self.config.get("ai_model", "claude-haiku-4-5-20251001")
        thread = AIThread(prompt, provider, api_key, model)
        thread.result_ready.connect(self._on_ai_result)
        self._ai_thread = thread
        thread.start()

    def _on_ai_result(self, text: str):
        self._ai_thread = None
        if self._ai_result_panel:
            self._ai_result_panel['loading'] = False
            self._ai_result_panel['result'] = text
            self._ai_result_panel['scroll'] = 0
        self._ai_select_mode = None
        self.update()

    def _paint_ai_result(self, painter: QPainter):
        panel = self._ai_result_panel
        if not panel or not panel.get('visible'):
            return
        vp = self._vp()
        panel_h = int(vp.height() * 0.25)
        panel_y = vp.bottom() - panel_h
        rect = QRect(vp.left(), panel_y, vp.width(), panel_h)

        painter.fillRect(rect, UI_BG)
        painter.setPen(_mk_pen(AMBER_BRIGHT, self._bw()))
        painter.drawRect(rect)
        painter.setPen(AMBER_BRIGHT)
        painter.setFont(_ui_font(9, bold=True))
        painter.drawText(rect.left()+8, rect.top()+18, "AI ANALYSIS")

        text_rect = QRect(rect.left()+8, rect.top()+28,
                          rect.width()-16, rect.height()-36)
        painter.setClipRect(text_rect)

        if panel['loading']:
            dots = ['.', '..', '...', '....']
            frame = (self._translate_anim_frame // 2) % len(dots)
            painter.setPen(AMBER_DIM)
            painter.setFont(_ui_font(9))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                             f"analyzing{dots[frame]}")
        else:
            painter.setPen(AMBER)
            painter.setFont(_ui_font(9))
            text = panel['result']
            scroll = panel.get('scroll', 0)
            words = text.split()
            lines = []
            cur = ""
            fm = QFontMetrics(_ui_font(9))
            for w in words:
                test = (cur + " " + w).strip()
                if fm.horizontalAdvance(test) <= text_rect.width():
                    cur = test
                else:
                    if cur: lines.append(cur)
                    cur = w
            if cur: lines.append(cur)

            line_h = fm.height() + 2
            y = text_rect.top() - scroll
            for line in lines:
                if y + line_h > text_rect.bottom():
                    break
                if y + line_h > text_rect.top():
                    painter.drawText(text_rect.left(), y + line_h - 2, line)
                y += line_h

        painter.setClipping(False)
        painter.setPen(AMBER_DARK)
        painter.setFont(_ui_font(8))
        painter.drawText(rect.left()+8, rect.bottom()-6,
                         "Esc dismiss   ↑/↓ scroll" if not panel['loading'] else "Esc dismiss")

    def _ai_result_scroll(self, delta: int):
        if self._ai_result_panel:
            scroll = self._ai_result_panel.get('scroll', 0)
            self._ai_result_panel['scroll'] = max(0, scroll + delta)
            self.update()

    def _clear_status(self):
        self.status_text = ""
        self.update()

    # Command processing (shortened for space – includes AI commands)

    CMD_ALIASES = {
        "bs": "bookset", "setm": "setmeta", "bi": "bookinfo", "sc": "showconfig",
        "ms": "ms", "vb": "vb", "vn": "vn", "vh": "vh", "lib": "lib",
        "hlh": "bookset highlight_height", "hla": "bookset highlight_alpha",
        "hlo": "bookset highlight_offset", "hlc": "bookset indicator_color",
        "shc": "bookset saved_highlight_color", "sha": "bookset saved_highlight_alpha",
        "bmc": "bookset bookmark_color", "ntc": "bookset note_color",
    }

    def _run(self, text: str) -> Optional[str]:
        parsed = parse_shortcut(text)
        if parsed:
            return self._exec_shortcut(parsed)

        parts = text.split(None, 2)
        if not parts: return None

        two = " ".join(parts[:2]).lower() if len(parts) >= 2 else ""
        one = parts[0].lower()
        if two in self.CMD_ALIASES:
            rest = (" " + parts[2]) if len(parts) > 2 else ""
            text = self.CMD_ALIASES[two] + rest
            parts = text.split(None, 2)
        elif one in self.CMD_ALIASES:
            rest = (" " + " ".join(parts[1:])) if len(parts) > 1 else ""
            text = self.CMD_ALIASES[one] + rest
            parts = text.split(None, 2)

        cmd = parts[0].lower()

        if cmd in ("q","quit","exit"):         QApplication.quit(); return None
        if cmd == "open":
            if len(parts) < 2: return "usage: open <path>"
            path = os.path.expanduser(" ".join(parts[1:]))
            ext  = Path(path).suffix.lower()
            if ext not in EBOOK_EXTS:
                return f"unsupported format: {ext}  (supported: {', '.join(sorted(EBOOK_EXTS))})"
            self.load_document(path); return None
        if cmd in ("lib", "library"):
            self.window().show_library(); return None
        if cmd == "fav":
            if not self.document: return "no document open"
            return self.history.set_favorite(self.document.filepath, True)
        if cmd == "unfav":
            if not self.document: return "no document open"
            return self.history.set_favorite(self.document.filepath, False)
        if cmd == "zoom":
            if len(parts) < 2: return f"zoom: {self.zoom_mode}  options: {', '.join(ZOOM_MODES+['cycle'])}"
            return self._do_zoom(parts[1].strip())
        if cmd in ("vn","viewnotes"):        return self._open_annot_panel("notes")
        if cmd in ("vb","viewbookmarks"):    return self._open_annot_panel("bookmarks")
        if cmd in ("vh","viewhighlights"):   return self._open_annot_panel("highlights")
        if cmd in ("ms","swapmargin"):
            side = "left" if self._margin_side() == "right" else "right"
            self.config.set("margin_side", side)
            if self.document: self._rerender()
            return f"margin: {side}"
        if cmd in ("fliplib", "flip"):
            flip = not bool(self.config.get("library_flip_mode"))
            self.config.set("library_flip_mode", flip)
            return f"library sizing: {'pages read' if flip else 'pages remaining'}"
        if cmd in ("tl", "translateline"):
            if not self.document: return "no document open"
            self._translate_line()
            return None
        if cmd in ("wizard", "setup"):
            self._open_wizard("app"); return None
        if cmd in ("bookwizard", "bwizard"):
            if not self.document: return "no document open"
            self._open_wizard("book"); return None
        if cmd in ("help","man","?"):        return self._open_panel("ScrollReader — Command Reference", "help")

        if cmd in ("sn","sp","sf","sl"):
            if len(parts) < 2: return "usage: sn/sp/sf/sl <term> or ;;phrase;;"
            raw = " ".join(parts[1:])
            m = re.search(r';;(.*?);;', raw)
            term = m.group(1) if m else raw.strip()
            result = self._do_search(term, cmd)
            self._last_command = text
            return result

        if cmd == "cc":
            if self._last_command:
                return self._run(self._last_command)
            return "no last command"
        if cmd == "set":
            if len(parts) < 3: return "usage: set <key> <value>"
            return self.config.set(parts[1], parts[2])
        if cmd == "bookset":
            if len(parts) < 3 or not self.document: return "usage: bookset <key> <value>"
            return self.history.set_override(self.document.filepath, parts[1], parts[2])
        if cmd == "showconfig":  return "  ".join(f"{k}={v}" for k,v in self.config.data.items())
        if cmd == "bookinfo":
            if not self.document: return "no document open"
            return self.history.summary(self.document.filepath)
        if cmd in ("setmeta", "setm"):
            if len(parts) < 3 or not self.document: return "usage: setmeta <field> <value>"
            return self.history.set_meta(self.document.filepath, parts[1], " ".join(parts[2:]))

        # AI commands
        if cmd in ('ebl', 'ebp', 'ehl', 'ehp', 'ccl', 'ccp'):
            rest = parts[1] if len(parts) > 1 else ''
            if rest == '0':
                if not self._last_ai_selection:
                    return "no previous selection"
                sel = self._last_ai_selection
                self._enter_ai_selection(sel['type'], sel['context'], sel['unit'], cmd)
                return None
            num = 1
            if rest.isdigit():
                num = int(rest)
            if cmd in ('ebl', 'ccl'):
                selection_type = 'bookmarks'; unit = 'line'
            elif cmd == 'ebp':
                selection_type = 'bookmarks'; unit = 'page'
            elif cmd == 'ehl':
                selection_type = 'highlights'; unit = 'line'
            elif cmd == 'ehp':
                selection_type = 'highlights'; unit = 'page'
            else:  # ccp
                selection_type = 'bookmarks'; unit = 'page'
            self._enter_ai_selection(selection_type, num, unit, cmd)
            return None

        return f"unknown: {cmd}  (help for list)"

    def _do_zoom(self, mode) -> str:
        if mode == "cycle":
            idx  = ZOOM_MODES.index(self.zoom_mode) if self.zoom_mode in ZOOM_MODES else 0
            mode = ZOOM_MODES[(idx+1) % len(ZOOM_MODES)]
        if mode not in ZOOM_MODES:
            return f"unknown zoom: {mode}  options: {', '.join(ZOOM_MODES+['cycle'])}"
        self.zoom_mode = mode
        self.config.data["zoom_mode"] = mode; self.config.save()
        if self.document: self._rerender()
        return f"zoom: {mode}"

    def _do_search(self, term: str, direction: str) -> str:
        if not self.document or not self.document.lines:
            return "no document open"
        try:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
        except re.error:
            return f"invalid search term: {term}"

        self._search_term = term
        self._search_re   = pattern
        lines  = self.document.lines
        total  = len(lines)
        cur    = self.current_line

        if direction == "sf":
            indices = range(total)
        elif direction == "sl":
            indices = range(total-1, -1, -1)
        elif direction == "sn":
            indices = list(range(cur+1, total)) + list(range(0, cur))
        elif direction == "sp":
            indices = list(range(cur-1, -1, -1)) + list(range(total-1, cur, -1))
        else:
            indices = range(total)

        wrapped = False
        for i in indices:
            if pattern.search(lines[i].text):
                if direction in ("sn", "sp"):
                    if direction == "sn" and i <= cur:
                        wrapped = True
                    elif direction == "sp" and i >= cur:
                        wrapped = True
                self._push_history(self.current_line)
                self.current_line = i
                self.history.set_line(self.document.filepath, i)
                self.update()
                wrap_note = "  [wrapped]" if wrapped else ""
                return f"found '{term}' at line {i+1}{wrap_note}"
        return f"'{term}' not found"

    def _exec_shortcut(self, p: dict) -> Optional[str]:
        if not self.document and p["cmd"] not in ("removeall_plus",):
            if not self.document: return "no document open"

        doc   = self.document
        lines = doc.lines if doc else []
        total = len(lines)
        cur   = self.current_line
        cmd   = p["cmd"]

        if cmd == "goto_line":
            self.current_line = max(0, min(total-1, p["line"]-1))
            self.history.set_line(doc.filepath, self.current_line)
            self.update(); return f"line {self.current_line+1}"
        if cmd == "goto_page":
            pg = max(0, min(doc.page_count-1, p["page"]-1))
            for i, l in enumerate(lines):
                if l.page_num == pg: self.current_line = i; break
            self.history.set_line(doc.filepath, self.current_line)
            self.update(); return f"page {pg+1}"
        if cmd == "line_back":    self._step(-p["count"]); return None
        if cmd == "line_forward": self._step(p["count"]);  return None
        if cmd == "page_back":
            for _ in range(p["count"]): self._jump_pages(-1); return None
        if cmd == "page_forward":
            for _ in range(p["count"]): self._jump_pages(1);  return None

        if cmd == "note":
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, _ = self._resolve_line_range(rs)
            return self.history.add_note(doc.filepath, sl, p.get("note",""))

        if cmd == "audio_note":
            if not HAS_AUDIO: return "audio not available"
            rs   = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, _ = self._resolve_line_range(rs)
            if _audio_recorder.recording:
                path = _audio_path(doc.filepath, sl)
                if _audio_recorder.stop_and_save(path):
                    self._vu_active = False
                    return self.history.add_audio_note(doc.filepath, sl, path)
                return "recording failed"
            else:
                if _audio_recorder.start():
                    self._vu_active = True
                    self.update()
                    return f"recording... run 'an' again to stop at L{sl+1}"
                return "failed to start recording"

        if cmd == "remove_audio_note":
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, el = self._resolve_line_range(rs)
            e  = self.history._entry(doc.filepath)
            an = e.get("audio_notes", [])
            remove = [n for n in an if sl <= n.get("line", 0) <= el]
            if not remove: return f"no audio notes in range"
            def _do():
                for item in remove:
                    try: os.remove(item["audio_path"])
                    except: pass
                e["audio_notes"] = [n for n in an if n not in remove]
                self.history._save()
                return f"removed {len(remove)} audio note(s)"
            self._pending = {"msg": f"remove {len(remove)} audio note(s)?", "action": _do}
            self.update()
            return None

        if cmd == "bookmark_line":
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, _ = self._resolve_line_range(rs)
            return self.history.add_bookmark(doc.filepath, sl, lines[sl].page_num, p.get("note"))

        if cmd == "bookmark_page":
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sp, _ = self._resolve_page_range(rs)
            fl = next((i for i, l in enumerate(lines) if l.page_num == sp), cur)
            return self.history.add_bookmark(doc.filepath, fl, sp, p.get("note"))

        if cmd == "highlight_line":
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, el = self._resolve_line_range(rs)
            result = self.history.add_highlight(doc.filepath, sl, el, p.get("note"))
            self.update(); return result

        if cmd == "highlight_page":
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sp, ep = self._resolve_page_range(rs)
            sl, el = self._page_range_to_line_range(sp, ep)
            result = self.history.add_highlight(doc.filepath, sl, el, p.get("note"))
            self.update(); return result

        if cmd == "removeall_plus":
            name = Path(self.document.filepath).name
            def action():
                self.history.remove_all_plus(self.document.filepath)
                self.update()
                return f"all data removed for {name}"
            self._pending = {"msg": f"Wipe ALL stored data for\n{name}?", "action": action}
            self.update(); return None

        if cmd == "removeall":
            def action():
                n = self.history.remove_all(doc.filepath, p.get("reason",""))
                self.update()
                return f"removed {n} annotation(s)"
            count = sum(len(self.history._entry(doc.filepath).get(k,[])) for k in ALL_ANNOTATION_KINDS)
            self._pending = {"msg": f"Remove ALL {count} annotation(s)?", "action": action}
            self.update(); return None

        if cmd in ("remove_line", "remove_page", "remove_bookmark", "remove_note", "remove_highlight"):
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            kinds = ALL_ANNOTATION_KINDS if cmd in ("remove_line","remove_page") else \
                    ["bookmarks"] if cmd == "remove_bookmark" else \
                    ["notes"] if cmd == "remove_note" else ["highlights"]
            if cmd in ("remove_line", "remove_bookmark", "remove_note", "remove_highlight"):
                sl, el = self._resolve_line_range(rs)
            else:
                sp, ep = self._resolve_page_range(rs)
                sl, el = self._page_range_to_line_range(sp, ep)
            count = self.history.count_in_range(doc.filepath, sl, el, kinds)
            reason = p.get("reason","")
            def action(sl=sl, el=el, kinds=kinds, reason=reason):
                n, desc = self.history.remove_annotations(doc.filepath, sl, el, kinds, reason)
                self.update()
                return f"removed: {desc}"
            lbl = f"lines {sl+1}–{el+1}" if sl != el else f"line {sl+1}"
            self._pending = {"msg": f"Remove {count} annotation(s) touching {lbl}?", "action": action}
            self.update(); return None

        if cmd in ("export_all", "export_line", "export_page",
                   "export_bookmark", "export_note", "export_highlight"):
            return self._do_export(cmd, p)

        if cmd == "print_dialog":
            return self._do_print(0, doc.page_count-1, show_dialog=True)
        if cmd == "print_pages":
            rs = p.get("range", RangeSpec("current"))
            if rs.mode == "error": return f"range error: {rs.error}"
            sp, ep = self._resolve_page_range(rs)
            return self._do_print(sp, ep, show_dialog=False)

        return f"unhandled: {cmd}"

    def _do_export(self, cmd, p) -> str:
        if not self.document: return "no document open"
        doc = self.document
        lines = doc.lines; total = len(lines)
        fp  = doc.filepath
        e   = self.history._entry(fp)

        kinds = ALL_ANNOTATION_KINDS if cmd in ("export_all","export_line","export_page") else \
                ["bookmarks"] if cmd == "export_bookmark" else \
                ["notes"] if cmd == "export_note" else ["highlights"]

        if cmd == "export_all":
            items = {k: e.get(k,[]) for k in kinds}
            subtitle = "All annotations"
        elif cmd in ("export_line", "export_bookmark", "export_note", "export_highlight"):
            rs = p.get("range", RangeSpec("current"))
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, el = self._resolve_line_range(rs)
            items = self.history.collect_in_range(fp, sl, el, kinds)
            subtitle = f"Lines {sl+1}–{el+1}" if sl != el else f"Line {sl+1}"
        else:
            rs = p.get("range", RangeSpec("current"))
            if rs.mode == "error": return f"range error: {rs.error}"
            sp, ep = self._resolve_page_range(rs)
            sl, el = self._page_range_to_line_range(sp, ep)
            items = self.history.collect_in_range(fp, sl, el, kinds)
            subtitle = f"Pages {sp+1}–{ep+1}" if sp != ep else f"Page {sp+1}"

        count = sum(len(v) for v in items.values())
        if count == 0: return "nothing to export"

        title = e.get("title") or Path(fp).stem
        short = re.sub(r"[^\w\s-]", "", title)[:25].strip().replace(" ", "_")
        export_dir = self.config.get("export_dir") or ""
        if not export_dir: export_dir = str(Path(fp).parent)
        os.makedirs(export_dir, exist_ok=True)

        mode = self.config.get("export_mode") or "timestamped"
        md   = build_export_md(title, items, subtitle)

        if mode == "running":
            fname = f"{short}_running.md"
            fpath = os.path.join(export_dir, fname)
            with open(fpath, "a", encoding="utf-8") as f:
                f.write("\n\n---\n\n"); f.write(md)
        else:
            ts    = time.strftime("%Y%m%d_%H%M%S")
            fname = f"{short}_{ts}.md"
            fpath = os.path.join(export_dir, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(md)

        return f"exported {count} annotation(s) → {fname}"

    def _do_print(self, start_page: int, end_page: int, show_dialog: bool = True) -> str:
        if not HAS_PRINT:
            return "printing not available (PyQt6.QtPrintSupport not installed)"
        if not self.document:
            return "no document open"

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(Path(self.document.filepath).stem)

        if show_dialog:
            dialog = QPrintDialog(printer, self)
            if dialog.exec() != QPrintDialog.DialogCode.Accepted:
                return "print cancelled"
            if printer.printRange() == QPrinter.PrintRange.PageRange:
                start_page = max(0, printer.fromPage() - 1)
                end_page   = min(self.document.page_count - 1, printer.toPage() - 1)

        self.status_text = f"printing pages {start_page+1}–{end_page+1}…"
        self.update(); QApplication.processEvents()

        try:
            paint = QPainter(printer)
            rect  = paint.viewport()
            dpi   = printer.resolution()
            mat   = fitz.Matrix(dpi / 72, dpi / 72)

            for i, pn in enumerate(range(start_page, end_page + 1)):
                if i > 0:
                    printer.newPage()
                page = self.document.doc[pn]
                pix  = page.get_pixmap(matrix=mat, alpha=False)
                img  = QImage(pix.samples, pix.width, pix.height,
                              pix.stride, QImage.Format.Format_RGB888)
                scaled = img.scaled(rect.width(), rect.height(),
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
                x = (rect.width()  - scaled.width())  // 2
                y = (rect.height() - scaled.height()) // 2
                paint.drawImage(x, y, scaled)
            paint.end()
        except Exception as ex:
            return f"print error: {ex}"

        count = end_page - start_page + 1
        return f"sent {count} page{'s' if count > 1 else ''} to printer"


# ---------------------------------------------------------------------------
# Audio recorder (full)
# ---------------------------------------------------------------------------

_audio_recorder = AudioRecorder()  # defined earlier

def _audio_dir() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = str(Path.home() / ".scrollreader")
    return os.path.join(base, "audio")

def _audio_path(filepath: str, line: int) -> str:
    book_hash = hashlib.md5(filepath.encode()).hexdigest()[:8]
    ts        = int(time.time())
    return os.path.join(_audio_dir(), f"{book_hash}_L{line}_{ts}.ogg")


# ---------------------------------------------------------------------------
# GamepadManager (full, simplified version)
# ---------------------------------------------------------------------------

GAMEPAD_DEFAULTS = {
    "btn_a": 0, "btn_b": 1, "btn_x": 2, "btn_y": 3,
    "btn_l1": 4, "btn_r1": 5, "btn_view": 6, "btn_menu": 7,
    "btn_l3": 8, "btn_r3": 9, "btn_dup": 11, "btn_ddown": 12,
    "btn_dleft": 13, "btn_dright": 14, "btn_l4": 15, "btn_l5": 16,
    "btn_r4": 17, "btn_r5": 18, "axis_lx": 0, "axis_ly": 1,
    "axis_rx": 2, "axis_ry": 3, "axis_lt": 4, "axis_rt": 5,
    "axis_threshold": 0.3, "scroll_repeat_ms": 120, "click_window_ms": 350,
}
MODE_CYCLE = ["reading", "library", "bookmarks", "highlights", "notes", "audionotes"]

class GamepadManager:
    def __init__(self, main_window, config):
        self.mw = main_window
        self.config = config
        self.joy = None
        self._map = dict(GAMEPAD_DEFAULTS)
        self._click_times = {}
        self._axis_repeats = {}
        self._held_axes = {}
        self._hl_mode = False
        self._hl_start = 0
        self._hl_end = 0
        self._mode_idx = 0
        if HAS_PYGAME:
            pygame.init()
            pygame.joystick.init()
            self._connect_first()

    def _connect_first(self):
        if HAS_PYGAME and pygame.joystick.get_count() > 0:
            self.joy = pygame.joystick.Joystick(0)
            self.joy.init()

    def _btn(self, name): return self._map.get(name, GAMEPAD_DEFAULTS.get(name, -1))

    def poll(self):
        if not HAS_PYGAME: return
        pygame.event.pump()
        if not self.joy:
            self._connect_first()
            return
        for event in pygame.event.get([pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP]):
            if event.type == pygame.JOYBUTTONDOWN:
                self._on_button(event.button)
        for axis_name, axis_idx in [("lx",self._map["axis_lx"]),("ly",self._map["axis_ly"]),
                                    ("rx",self._map["axis_rx"]),("ry",self._map["axis_ry"]),
                                    ("lt",self._map["axis_lt"]),("rt",self._map["axis_rt"])]:
            try: val = self.joy.get_axis(axis_idx)
            except: val = 0.0
            self._held_axes[axis_name] = val
        self._process_axes()

    def _on_button(self, btn):
        now = time.time()
        clicks = self._click_times.setdefault(btn, [])
        window = self._map["click_window_ms"]/1000.0
        clicks[:] = [t for t in clicks if now-t < window]
        clicks.append(now)
        m = self._map
        reader = self.mw.reader
        if btn in (m["btn_b"], m["btn_l1"], m["btn_r1"]):
            self._go_back(); return
        if btn == m["btn_a"]:
            self._do_select(); return
        if btn == m["btn_x"]:
            self._cycle_mode(-1); return
        if btn == m["btn_y"]:
            self._cycle_mode(1); return
        if btn == m["btn_view"]:
            if time.time() > reader._cmd_cooldown:
                reader._enter_command_mode(); return
        if btn == m["btn_menu"]:
            reader._open_panel("ScrollReader — Command Reference", "help"); return
        if btn == m["btn_l3"]:
            QTimer.singleShot(int(window*1000)+50, lambda: self._open_config_popup("global")); return
        if btn == m["btn_r3"]:
            QTimer.singleShot(int(window*1000)+50, lambda: self._open_config_popup("book")); return
        if btn == m["btn_l4"]:
            QTimer.singleShot(int(window*1000)+50, lambda: self._l4_action()); return
        if btn == m["btn_l5"]:
            QTimer.singleShot(int(window*1000)+50, lambda: self._bookmark_action()); return
        if btn == m["btn_r4"]:
            QTimer.singleShot(int(window*1000)+50, lambda: self._search_action()); return
        if btn == m["btn_r5"]:
            QTimer.singleShot(int(window*1000)+50, lambda: self._highlight_action()); return

    def _l4_action(self):
        clicks = len(self._click_times.get(self._map["btn_l4"], []))
        if clicks == 2:
            self._audio_note_action()
        elif clicks >= 3:
            self._text_note_action()

    def _cycle_mode(self, direction):
        reader = self.mw.reader
        lib = self.mw.library
        self._mode_idx = (self._mode_idx + direction) % len(MODE_CYCLE)
        target = MODE_CYCLE[self._mode_idx]
        if lib.isVisible(): lib.hide()
        if reader._panel_mode: reader._panel_back()
        if target == "reading":
            reader.setFocus()
        elif target == "library":
            self.mw.show_library()
        elif target == "bookmarks":
            reader.setFocus(); reader._open_annot_panel("bookmarks")
        elif target == "highlights":
            reader.setFocus(); reader._open_annot_panel("highlights")
        elif target == "notes":
            reader.setFocus(); reader._open_annot_panel("notes")
        elif target == "audionotes":
            reader.setFocus(); reader._open_annot_panel("audionotes")

    def _go_back(self):
        reader = self.mw.reader
        lib = self.mw.library
        if self._hl_mode:
            self._hl_mode = False; reader.update(); return
        if lib.isVisible():
            lib.hide(); reader.setFocus(); self._mode_idx = 0; return
        if reader._panel_mode:
            reader._panel_back(); self._mode_idx = 0; return

    def _do_select(self):
        reader = self.mw.reader
        lib = self.mw.library
        if self._hl_mode:
            self._save_highlight(); return
        if lib.isVisible():
            lib._select_current(); return
        if reader._panel_mode:
            reader._panel_select(); return
        reader._step(1)

    def _process_axes(self):
        now = time.time()
        rms = self._map["scroll_repeat_ms"]/1000.0
        thresh = self._map["axis_threshold"]
        reader = self.mw.reader
        lib = self.mw.library
        ly = self._held_axes.get("ly",0.0)
        ry = self._held_axes.get("ry",0.0)
        lx = self._held_axes.get("lx",0.0)

        def _fire(key, val, action):
            if abs(val) > thresh:
                last = self._axis_repeats.get(key,0)
                if now - last > rms:
                    self._axis_repeats[key] = now
                    action(val)
            else:
                self._axis_repeats.pop(key,None)

        if lib.isVisible():
            _fire("lx", lx, lambda v: lib._cycle_tab(1 if v>0 else -1))
            rx = self._held_axes.get("rx",0.0)
            _fire("rx", rx, lambda v: lib._move_cursor_by_axis(v, ry))
        elif self._hl_mode:
            _fire("ry_hl_end", ry, lambda v: self._hl_extend("end", 1 if v>0 else -1))
            _fire("ly_hl_start", ly, lambda v: self._hl_extend("start", 1 if v>0 else -1))
        elif reader._panel_mode:
            _fire("ly", ly, lambda v: reader._panel_navigate(1 if v>0 else -1))
        else:
            _fire("ly", ly, lambda v: reader._step(1 if v>0 else -1))
            _fire("ry", ry, lambda v: reader._step(1 if v>0 else -1))

    def _hl_extend(self, which, direction):
        reader = self.mw.reader
        if not reader.document: return
        total = len(reader.document.lines)
        if which == "end":
            self._hl_end = max(self._hl_start, min(total-1, self._hl_end + direction))
        else:
            self._hl_start = min(self._hl_end, max(0, self._hl_start + direction))
        reader.update()

    def _highlight_action(self):
        reader = self.mw.reader
        if not reader.document: return
        clicks = len(self._click_times.get(self._map["btn_r5"], []))
        if clicks >= 2:
            if self._hl_mode:
                self._save_highlight()
            else:
                self._hl_mode = True
                self._hl_start = reader.current_line
                self._hl_end = reader.current_line
                reader.status_text = "HIGHLIGHT MODE — right stick extend, double R5 to save"
                reader.update()

    def _save_highlight(self):
        reader = self.mw.reader
        if not reader.document: return
        reader.history.add_highlight(reader.document.filepath, self._hl_start, self._hl_end, "")
        self._hl_mode = False
        reader.status_text = f"highlight saved: L{self._hl_start+1}–L{self._hl_end+1}"
        QTimer.singleShot(3000, reader._clear_status)
        reader.update()

    def _bookmark_action(self):
        reader = self.mw.reader
        if not reader.document: return
        reader.history.add_bookmark(reader.document.filepath, reader.current_line, "")
        reader.status_text = f"bookmark saved: L{reader.current_line+1}"
        QTimer.singleShot(3000, reader._clear_status)
        reader.update()

    def _text_note_action(self):
        reader = self.mw.reader
        if not reader.document: return
        reader._enter_command_mode()
        reader.cmd.setText(":nl ")
        reader.cmd.setCursorPosition(len(reader.cmd.text()))

    def _audio_note_action(self):
        reader = self.mw.reader
        if not reader.document or not HAS_AUDIO: return
        if _audio_recorder.recording:
            path = _audio_path(reader.document.filepath, reader.current_line)
            if _audio_recorder.stop_and_save(path):
                reader.history.add_audio_note(reader.document.filepath, reader.current_line, path)
                reader.status_text = "audio note saved"
                QTimer.singleShot(3000, reader._clear_status)
                reader._vu_active = False
                reader.update()
        else:
            if _audio_recorder.start():
                reader.status_text = "● REC"
                reader._vu_active = True
                reader.update()

    def _search_action(self):
        reader = self.mw.reader
        if not reader.document: return
        reader._open_search_panel()

    def _open_config_popup(self, kind):
        self.mw.reader._open_config_popup(kind)

    def get_hl_range(self):
        return self._hl_start, self._hl_end, self._hl_mode


# ---------------------------------------------------------------------------
# LibraryWidget (full version – treemap library)
# ---------------------------------------------------------------------------

def _book_color(filepath: str, config) -> str:
    base = QColor(config.get("theme_primary") or "#ffbb33")
    h, s, v, _ = base.getHsvF()
    slot = hash(filepath) % 12
    hshift = (slot - 6) / 60.0
    vh = (h + hshift) % 1.0
    vv = max(0.3, min(1.0, v - 0.05 + (slot % 4) * 0.05))
    return QColor.fromHsvF(vh, s * 0.85, vv).name()

def _scan_pdfs(directory: str, recursive: bool = False) -> list[str]:
    p = Path(directory)
    if not p.exists(): return []
    if recursive:
        return [str(f) for f in p.rglob("*.pdf")]
    else:
        return [str(f) for f in p.glob("*.pdf")]

def _default_lib_dir() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    if sys.platform.startswith("win"):
        return base
    return str(Path.home() / "library")

def _squarify(books, weights, x, y, w, h, padding, min_w, min_h):
    if not books or not weights or w <= 0 or h <= 0:
        return []
    pairs = sorted(zip(books, weights), key=lambda p: p[1], reverse=True)
    books_s = [p[0] for p in pairs]
    weights_s = [p[1] for p in pairs]
    results = []
    def _layout(items, wts, rx, ry, rw, rh, depth=0):
        if not items or rw < min_w or rh < min_h:
            return
        if len(items) == 1:
            results.append((items[0], QRect(int(rx)+padding, int(ry)+padding,
                          max(min_w, int(rw)-padding*2), max(min_h, int(rh)-padding*2))))
            return
        wt_total = sum(wts)
        cumulative = 0; split_idx = 1; best_diff = float('inf')
        for i in range(1, len(wts)):
            cumulative += wts[i-1]
            diff = abs(cumulative / wt_total - 0.5)
            if diff < best_diff:
                best_diff = diff; split_idx = i
        frac_a = sum(wts[:split_idx]) / wt_total
        frac_b = 1.0 - frac_a
        if rw >= rh:
            wa = max(min_w, int(rw * frac_a))
            wb = max(min_w, int(rw * frac_b))
            wb = max(min_w, rw - wa)
            _layout(items[:split_idx], wts[:split_idx], rx, ry, wa, rh, depth+1)
            _layout(items[split_idx:], wts[split_idx:], rx+wa, ry, wb, rh, depth+1)
        else:
            ha = max(min_h, int(rh * frac_a))
            hb = max(min_h, int(rh * frac_b))
            hb = max(min_h, rh - ha)
            _layout(items[:split_idx], wts[:split_idx], rx, ry, rw, ha, depth+1)
            _layout(items[split_idx:], wts[split_idx:], rx, ry+ha, rw, hb, depth+1)
    _layout(books_s, weights_s, x, y, w, h)
    return results

LIBRARY_TABS = ["REFERENCE","SEARCH","FAVORITES","SETTINGS","READING","READ","UNREAD","ABANDONED"]
TAB_H = 38; TAG_BAR_H = 36; LIB_STATUS_H = 24
STATUS_SAT = {"reading":1.0,"unread":0.65,"favorites":1.0,"abandoned":0.15,"read":0.8}
def _adjust_sat(hex_color, factor):
    c = QColor(hex_color)
    h,s,v,a = c.getHsvF()
    c.setHsvF(h, min(1.0, s*factor), v, a)
    return c

class LibraryWidget(QWidget):
    def __init__(self, config: Config, history: History, parent=None):
        super().__init__(parent)
        self.config = config
        self.history = history
        self.tab = "READING"
        self.active_tag = None
        self._book_rects = []
        self._tab_rects = []
        self._cursor_idx = 0
        self._overflow_stack = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background: transparent;")
        self.hide()
        self.cmd = QLineEdit(self)
        self.cmd.setVisible(False)
        self.cmd.installEventFilter(self)
        self.cmd.setStyleSheet("QLineEdit { background-color: #000000; color: #ffbb33; border: none; border-top: 1px solid #553300; padding: 3px 10px; font-family: monospace; }")
        self.status_msg = ""
        self._lib_search_query = ""
        self._lib_search_results = {}
        self._lib_search_progress = {}
        self._lib_search_all_fps = []
        self._lib_search_scanning = False
        self._lib_search_cursor = 0
        self._lib_search_input_active = False
        self._lib_search_thread = None
        self._lib_refreshing = False
        self._lib_refresh_frac = 0.0
        self._loading_active = False
        self._loading_filename = ""
        self._loading_frame = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._loading_tick)
        self._loading_timer.start(130)

    def resizeEvent(self, ev):
        self.cmd.setGeometry(0, self.height()-26, self.width(), 26)
        super().resizeEvent(ev)

    def showEvent(self, ev):
        self._overflow_stack = []
        self._cursor_idx = 0
        self._refresh_books()
        self.setFocus()
        super().showEvent(ev)

    def _refresh_books(self):
        lib_dir = self.config.get("library_dir") or _default_lib_dir()
        recursive = bool(self.config.get("library_recursive") or False)
        found = _scan_pdfs(lib_dir, recursive)
        need_peek = [fp for fp in found if not self.history._entry(fp).get("total_pages")]
        if need_peek:
            self._lib_refreshing = True
            self._lib_refresh_frac = 0.0
            self.update()
        changed = False
        for i, fp in enumerate(need_peek):
            e = self.history._entry(fp)
            try:
                doc = fitz.open(fp)
                e["total_pages"] = len(doc)
                doc.close()
                changed = True
            except Exception:
                e["total_pages"] = 1
            self._lib_refresh_frac = (i+1)/max(len(need_peek),1)
            self.update()
        self._lib_refreshing = False
        if changed:
            self.history._save()

    def _all_books(self) -> list[dict]:
        books = []
        for fp, e in self.history.data.items():
            if not os.path.exists(fp): continue
            books.append({
                "filepath": fp,
                "title": e.get("title") or Path(fp).stem,
                "author": e.get("author") or "",
                "status": e.get("status") or "unread",
                "rating": e.get("rating") or 0,
                "tags": e.get("tags") or [],
                "favorite": bool(e.get("favorite")),
                "line": e.get("line") or 0,
                "total": e.get("total_lines") or (e.get("total_pages") or 1)*40,
                "total_pages": e.get("total_pages") or 1,
                "notes": len(e.get("notes",[])),
                "bookmarks": len(e.get("bookmarks",[])),
                "highlights": len(e.get("highlights",[])),
                "color": _book_color(fp, self.config),
            })
        return books

    def _books_for_tab(self, tab: str) -> list[dict]:
        books = self._all_books()
        if self.active_tag:
            books = [b for b in books if self.active_tag in b["tags"]]
        if tab == "READING":
            books = [b for b in books if b["status"]=="reading"]
            books.sort(key=lambda b: b["line"]/max(b["total"],1), reverse=True)
        elif tab == "UNREAD":
            books = [b for b in books if b["status"]=="unread"]
            books.sort(key=lambda b: b["total"], reverse=True)
        elif tab == "FAVORITES":
            books = [b for b in books if b["favorite"]]
            books.sort(key=lambda b: b["line"]/max(b["total"],1), reverse=True)
        elif tab == "ABANDONED":
            books = [b for b in books if b["status"]=="abandoned"]
            books.sort(key=lambda b: b["line"]/max(b["total"],1), reverse=True)
        elif tab == "READ":
            books = [b for b in books if b["status"]=="read"]
            books.sort(key=lambda b: b.get("title","").lower())
        return books

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0,0,w,h, UI_BG)
        self._tab_rects = []
        tw = w//4
        font_b = _ui_font(9,bold=True)
        painter.setFont(font_b)
        for i, tab in enumerate(LIBRARY_TABS):
            row = 0 if i<4 else 1
            col = i if i<4 else i-4
            x = col*tw
            y = row*TAB_H
            rect = QRect(x,y,tw-2,TAB_H-2)
            self._tab_rects.append((rect,tab))
            active = tab==self.tab
            painter.fillRect(rect, AMBER_INV_BG if active else UI_BG)
            painter.setPen(AMBER_BRIGHT if active else AMBER_DARK)
            painter.drawRect(rect)
            painter.setPen(AMBER_INV_FG if active else AMBER_DIM)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tab)
        content_y = TAB_H*2+4
        content_h = h - content_y - TAG_BAR_H - LIB_STATUS_H - (26 if self.cmd.isVisible() else 0)
        if self.tab == "SETTINGS":
            self._paint_settings(painter,0,content_y,w,content_h)
        else:
            self._paint_blocks(painter,0,content_y,w,content_h)
        tag_y = h - TAG_BAR_H - LIB_STATUS_H - (26 if self.cmd.isVisible() else 0)
        stat_y = h - LIB_STATUS_H - (26 if self.cmd.isVisible() else 0)
        self._paint_tag_bar(painter,0,tag_y,w)
        self._paint_lib_status(painter,0,stat_y,w)
        if self._loading_active:
            self._paint_loading_overlay(painter)

    def _paint_blocks(self, painter, x, y, w, h):
        self._book_rects = []
        books = self._books_for_tab(self.tab)
        if not books:
            painter.setPen(AMBER_DARK)
            painter.setFont(_ui_font(13))
            painter.drawText(QRect(x,y,w,h), Qt.AlignmentFlag.AlignCenter, f"No books in {self.tab.lower()}")
            return
        sat = STATUS_SAT.get(self.tab.lower(),1.0)
        padding=4; MIN_W=100; MIN_H=60; MIN_AREA=MIN_W*MIN_H
        VP_W = w-padding*2; VP_H = h-padding*2; VP_AREA = VP_W*VP_H
        def _unread(b):
            tp = max(b["total_pages"],1)
            read = round(b["line"]/max(b["total"],1)*tp)
            flip = bool(self.config.get("library_flip_mode"))
            if self.tab=="READ":
                return tp
            if flip:
                return max(1,read)
            else:
                return max(1,tp-read)
        unread = [_unread(b) for b in books]
        scaled = [max(1, u**0.25) for u in unread]
        total_s = max(sum(scaled),1)
        norm_areas = [(s/total_s)*VP_AREA for s in scaled]
        visible = [(b,a) for b,a in zip(books, norm_areas) if a>=MIN_AREA]
        ov_books = [b for b,a in zip(books, norm_areas) if a<MIN_AREA]
        ov_area = sum(a for b,a in zip(books, norm_areas) if a<MIN_AREA)
        if not visible and ov_books:
            visible = [(b,MIN_AREA) for b in books]
            ov_books = []; ov_area = 0
        if ov_books:
            OVFL = {"_overflow":True}
            layout_books = [b for b,_ in visible]+[OVFL]
            layout_areas = [a for _,a in visible]+[max(MIN_AREA,ov_area)]
        else:
            layout_books = [b for b,_ in visible]; layout_areas = [a for _,a in visible]
        all_rects = _squarify(layout_books, layout_areas, x+padding, y+padding, VP_W, VP_H, padding, MIN_W, MIN_H)
        mono = _ui_font(9); mono_b = _ui_font(9,bold=True)
        for idx, (b, rect) in enumerate(all_rects):
            is_overflow = b.get("_overflow",False) if isinstance(b,dict) else False
            selected = (idx==self._cursor_idx)
            if is_overflow:
                painter.fillRect(rect, AMBER_INV_BG if selected else UI_BG)
                xfg = AMBER_INV_FG if selected else AMBER_DIM
                painter.setFont(_ui_font(9))
                painter.setPen(xfg)
                fm = QFontMetrics(_ui_font(9))
                cw = max(fm.horizontalAdvance("X"),1); ch=14
                for row in range(rect.top()+4, rect.bottom()-18, ch):
                    for col in range(rect.left()+4, rect.right()-4, cw):
                        painter.drawText(col, row+ch-2, "X")
                label = f"+{len(ov_books)} more"
                cats = {"unread":0,"reading":0,"read":0,"abandoned":0}
                for b in ov_books: s = b.get("status","unread"); if s in cats: cats[s]+=1
                cat_str = ""
                if cats["unread"]: cat_str+=f"{cats['unread']}U"
                if cats["reading"]: cat_str+=f"{cats['reading']}R"
                if cats["read"]: cat_str+=f"{cats['read']}D"
                if cats["abandoned"]: cat_str+=f"{cats['abandoned']}A"
                label = f"+{len(ov_books)}/{cat_str}" if cat_str else f"+{len(ov_books)}"
                painter.fillRect(QRect(rect.left(), rect.bottom()-18, rect.width(),18), UI_BG)
                painter.setPen(AMBER_BRIGHT if selected else AMBER)
                painter.setFont(_ui_font(9,bold=True))
                painter.drawText(QRect(rect.left(), rect.bottom()-18, rect.width(),18), Qt.AlignmentFlag.AlignCenter, label)
                painter.setPen(AMBER_BRIGHT if selected else AMBER_DARK)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
                self._book_rects.append((rect,None))
            else:
                color = _adjust_sat(b["color"], sat)
                if selected:
                    painter.fillRect(rect, AMBER_INV_BG)
                    text_primary = AMBER_INV_FG
                else:
                    painter.fillRect(rect, color)
                    painter.fillRect(rect, QColor(0,0,0,120))
                    text_primary = AMBER_BRIGHT
                accent = AMBER_INV_FG if selected else _adjust_sat(b["color"], min(sat*1.3,1.0))
                painter.fillRect(QRect(rect.x(), rect.y(),4,rect.height()), accent)
                painter.setPen(AMBER_BRIGHT if selected else QColor(255,255,255,20))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
                if b["favorite"]:
                    painter.setPen(AMBER_BRIGHT if not selected else AMBER_INV_FG)
                    painter.setFont(_ui_font(10))
                    painter.drawText(QRect(rect.x()+rect.width()-20, rect.y()+4,16,16), Qt.AlignmentFlag.AlignCenter, "★")
                inner = QRect(rect.x()+10, rect.y()+8, rect.width()-20, rect.height()-16)
                line_h = 16
                metar = f"N{b['notes']}B{b['bookmarks']}H{b['highlights']}R{int(b['line']/max(b['total'],1)*100)}P{b['total_pages']}"
                txt_col = AMBER_INV_FG if selected else AMBER_BRIGHT
                texts = [(metar, mono, txt_col), (b["title"], mono_b, txt_col), (b["author"], mono, txt_col)]
                ty = inner.top()
                for txt, font, col in texts:
                    if ty+line_h>inner.bottom(): break
                    painter.setFont(font); painter.setPen(col)
                    painter.drawText(rect.x()+10, ty+line_h-2, QFontMetrics(font).elidedText(txt, Qt.TextElideMode.ElideRight, inner.width()))
                    ty += line_h
                self._book_rects.append((rect, b["filepath"]))
        if self._cursor_idx >= len(self._book_rects):
            self._cursor_idx = max(0, len(self._book_rects)-1)

    def _paint_tag_bar(self, painter, x, y, w):
        painter.fillRect(QRect(x,y,w,TAG_BAR_H), UI_BG)
        painter.setPen(AMBER_DARK)
        painter.drawLine(x,y,x+w,y)
        txt = ""
        if self._book_rects and self._cursor_idx < len(self._book_rects):
            _, fp = self._book_rects[self._cursor_idx]
            if fp is not None:
                e = self.history._entry(fp)
                tp = e.get("total_pages") or 1
                ln = e.get("line") or 0
                tl = max((e.get("total_lines") or 1),1)
                n = len(e.get("notes",[]))
                b = len(e.get("bookmarks",[]))
                h = len(e.get("highlights",[]))
                r = int(ln/tl*100)
                metar = f"N{n}B{b}H{h}R{r}P{tp}"
                fname = os.path.basename(fp)
                txt = f"{metar}   {fname}"
            elif fp is None:
                txt = f"+{len([b for b in self._books_for_tab(self.tab)])} overflow books"
        painter.setPen(AMBER_DIM)
        painter.setFont(_ui_font(9))
        painter.drawText(x+10, y+TAG_BAR_H-8, txt or "—")

    def _paint_lib_status(self, painter, x, y, w):
        painter.fillRect(QRect(x,y,w,LIB_STATUS_H), UI_BG)
        painter.setPen(AMBER_VERY_DIM)
        painter.drawLine(x,y,x+w,y)
        painter.setPen(AMBER_DARK)
        painter.setFont(_ui_font(9))
        books = self._books_for_tab(self.tab)
        depth = f"  [+{len(self._overflow_stack)} levels deep]" if self._overflow_stack else ""
        msg = self.status_msg or (f"{len(books)} book(s){depth}  —  ↑↓←→ navigate  Space: open  Tab/Esc: back  Enter: command")
        painter.drawText(x+12, y+LIB_STATUS_H-6, msg)
        tasks = []
        if getattr(self,'_lib_search_scanning',False):
            done = sum(1 for v in self._lib_search_progress.values() if v>=1.0)
            total = max(len(self._lib_search_all_fps),1)
            tasks.append((done/total, AMBER))
        if self._lib_refreshing:
            tasks.append((getattr(self,'_lib_refresh_frac',0.5), AMBER_DIM))
        if tasks:
            bar_w=6; bar_gap=3; bar_h=LIB_STATUS_H-6
            total_w = len(tasks)*(bar_w+bar_gap)-bar_gap
            bx = x+w-total_w-8
            by = y+3
            for frac,color in tasks:
                painter.fillRect(QRect(bx,by,bar_w,bar_h), AMBER_VERY_DIM)
                fill_h = max(2,int(bar_h*frac))
                painter.fillRect(QRect(bx,by+bar_h-fill_h,bar_w,fill_h), color)
                bx += bar_w+bar_gap

    def _paint_settings(self, painter, x, y, w, h):
        mono = _ui_font(10); mono_b = _ui_font(10,bold=True); head = _ui_font(10,bold=True)
        settings_ref = [
            ("LIBRARY", [("library_dir", self.config.get("library_dir") or _default_lib_dir(), "set library_dir <path>", "Root folder scanned for PDFs"),
                         ("library_recursive", str(self.config.get("library_recursive") or False), "set library_recursive true", "Scan subdirectories too"),
                         ("read_tab_sizing", self.config.get("read_tab_sizing") or "flat", "set read_tab_sizing lines", "Read tab: 'flat' (default) or 'lines'")]),
            ("APPEARANCE", [("current_swatch", self.config.get("current_swatch") or "amber", "ctrl+k/l to cycle", "Active colour swatch"),
                            ("indicator_color", self.config.get("indicator_color"), "ctrl+o/p to cycle", "Main line indicator colour"),
                            ("background_color", self.config.get("background_color"), "set background_color #1a1a1a", "Main background"),
                            ("statusbar_color", self.config.get("statusbar_color"), "set statusbar_color #111111", "Status bar background"),
                            ("statusbar_text_color", self.config.get("statusbar_text_color"), "set statusbar_text_color #888", "Status bar text"),
                            ("highlight_alpha", str(self.config.get("highlight_alpha")), "set highlight_alpha 35", "Highlight opacity (0–255)"),
                            ("highlight_height", str(self.config.get("highlight_height")), "set highlight_height 20", "Highlight band height px")]),
            ("EXPORT", [("export_dir", self.config.get("export_dir") or "(same as PDF)", "set export_dir ~/exports", "Export destination"),
                        ("export_mode", self.config.get("export_mode") or "timestamped", "set export_mode running", "timestamped or running")]),
            ("READING", [("reopen_last", str(self.config.get("reopen_last")), "set reopen_last true", "Auto-reopen last book"),
                         ("midpoint", str(self.config.get("midpoint")), "set midpoint 0.42", "Indicator lock position (0–1)"),
                         ("zoom_mode", str(self.config.get("zoom_mode")), "set zoom_mode fit-width", "Default zoom mode"),
                         ("page_gap", str(self.config.get("page_gap")), "set page_gap 30", "Gap between pages (px)")]),
        ]
        cur_y = y+12; lh_head=28; lh_row=22; col1=x+16; col2=x+240; col3=x+460
        painter.setClipRect(QRect(x,y,w,h))
        for section, rows in settings_ref:
            if cur_y > y+h: break
            painter.fillRect(QRect(x,cur_y,w,lh_head), QColor(10,8,0))
            painter.setPen(AMBER_BRIGHT); painter.setFont(head)
            painter.drawText(col1, cur_y+lh_head-6, section)
            cur_y += lh_head+2
            painter.setFont(mono)
            for key,val,cmd,desc in rows:
                if cur_y+lh_row > y+h: break
                painter.setPen(AMBER); painter.setFont(mono_b)
                painter.drawText(col1, cur_y+lh_row-5, key)
                painter.setPen(AMBER_DIM); painter.setFont(mono)
                elided_val = QFontMetrics(mono).elidedText(str(val), Qt.TextElideMode.ElideRight, col3-col2-16)
                painter.drawText(col2, cur_y+lh_row-5, elided_val)
                painter.setPen(AMBER_DARK)
                full_cmd = f"{cmd}   — {desc}"
                elided_cmd = QFontMetrics(mono).elidedText(full_cmd, Qt.TextElideMode.ElideRight, w-col3-20)
                painter.drawText(col3, cur_y+lh_row-5, elided_cmd)
                cur_y += lh_row
            cur_y += 8
        painter.setClipping(False)

    def _paint_loading_overlay(self, painter):
        FRAMES = [' >>>', '> >>', '>> >', '>>> ']
        arrow = FRAMES[self._loading_frame % len(FRAMES)]
        text = f"{arrow} loading {self._loading_filename}"
        font = _ui_font(11,bold=True)
        fm = QFontMetrics(font)
        pw = fm.horizontalAdvance(text)+48
        ph = 52
        px = (self.width()-pw)//2
        py = (self.height()-ph)//2
        painter.fillRect(QRect(px,py,pw,ph), UI_BG)
        painter.setPen(_mk_pen(AMBER_DARK,2))
        painter.drawRect(QRect(px,py,pw,ph))
        painter.setPen(AMBER_BRIGHT)
        painter.setFont(font)
        painter.drawText(QRect(px,py,pw,ph), Qt.AlignmentFlag.AlignCenter, text)

    def _loading_tick(self):
        if self._loading_active:
            self._loading_frame += 1
            self.update()

    def _select_current(self):
        if not self._book_rects: return
        idx = self._cursor_idx
        if idx >= len(self._book_rects): return
        _, fp = self._book_rects[idx]
        if fp is None:
            books = self._books_for_tab(self.tab)
            vp_area = self.width()*self.height()
            def _u(b): return max(1, b["total_pages"] - round(b["line"]/max(b["total"],1)*b["total_pages"]))
            unread = [_u(b) for b in books]
            total_u = max(sum(unread),1)
            norm = [(u/total_u)*vp_area for u in unread]
            MIN_AREA = 100*60
            overflow = [b for b,a in zip(books,norm) if a<MIN_AREA]
            if overflow:
                self._overflow_stack.append(overflow)
                self._cursor_idx = 0
                self.update()
        else:
            self._loading_active = True
            self._loading_filename = Path(fp).name
            self._loading_frame = 0
            self.update()
            QTimer.singleShot(50, lambda f=fp: self._do_open(f))

    def _do_open(self, fp):
        self._loading_active = False
        self.hide()
        self.open_book.emit(fp)

    def _go_back(self):
        if self._overflow_stack:
            self._overflow_stack.pop()
            self._cursor_idx = 0
            self.update()
        else:
            self.hide()
            self.parent().reader.setFocus()

    def _move_cursor(self, dx, dy):
        n = len(self._book_rects)
        if n==0: return
        cur = self._cursor_idx
        if cur>=n: cur=0
        rects = [r for r,_ in self._book_rects]
        cx,cy = rects[cur].center().x(), rects[cur].center().y()
        best_idx = cur; best_dist = float('inf')
        for i,r in enumerate(rects):
            if i==cur: continue
            rx, ry = r.center().x(), r.center().y()
            ddx, ddy = rx-cx, ry-cy
            if dx!=0 and (dx>0) != (ddx>0): continue
            if dy!=0 and (dy>0) != (ddy>0): continue
            if dx==0 and abs(ddx) > abs(ddy)*2: continue
            if dy==0 and abs(ddy) > abs(ddx)*2: continue
            dist = ddx*ddx + ddy*ddy
            if dist < best_dist:
                best_dist = dist; best_idx = i
        self._cursor_idx = best_idx
        self.update()

    def _move_cursor_by_axis(self, rx, ry):
        if not self._book_rects: return
        cur_idx = max(0, min(self._cursor_idx, len(self._book_rects)-1))
        cur_rect, _ = self._book_rects[cur_idx]
        cx, cy = cur_rect.center().x(), cur_rect.center().y()
        dx_want, dy_want = rx*200, ry*200
        best_idx, best_score = cur_idx, float('inf')
        for i, (rect, _) in enumerate(self._book_rects):
            if i==cur_idx: continue
            dx = rect.center().x()-cx
            dy = rect.center().y()-cy
            dot = dx*dx_want + dy*dy_want
            if dot > 0:
                score = (dx*dx+dy*dy)**0.5 / max(dot,0.001)
                if score < best_score:
                    best_score = score
                    best_idx = i
        if best_idx != cur_idx:
            self._cursor_idx = best_idx
            self.update()

    def _cycle_tab(self, direction):
        all_tabs = LIBRARY_TABS
        if self.tab not in all_tabs: self.tab = all_tabs[0]
        else:
            idx = all_tabs.index(self.tab)
            self.tab = all_tabs[(idx+direction)%len(all_tabs)]
        self._overflow_stack = []
        self._cursor_idx = 0
        self._refresh_books()
        self.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key.Key_Escape:
            self._go_back(); return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._select_current(); return
        if k in (Qt.Key.Key_Tab, Qt.Key.Key_Backspace):
            self._go_back(); return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_A):
            self._cycle_tab(-1); return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_D):
            self._cycle_tab(1); return
        if k in (Qt.Key.Key_Down, Qt.Key.Key_S):
            n = len(self._book_rects)
            if n: self._cursor_idx = min(n-1, self._cursor_idx+1)
            self.update(); return
        if k in (Qt.Key.Key_Up, Qt.Key.Key_W):
            if self._book_rects: self._cursor_idx = max(0, self._cursor_idx-1)
            self.update(); return
        if k == Qt.Key.Key_R:
            self._go_back(); return

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        n = len(self._book_rects)
        if n==0 or not delta: return
        if delta<0:
            self._cursor_idx = min(n-1, self._cursor_idx+1)
        else:
            self._cursor_idx = max(0, self._cursor_idx-1)
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton: return
        pos = ev.pos()
        for rect, tab in self._tab_rects:
            if rect.contains(pos):
                self.tab = tab
                self._overflow_stack = []
                self._cursor_idx = 0
                self._refresh_books()
                self.update()
                return
        for i, (rect, fp) in enumerate(self._book_rects):
            if rect.contains(pos):
                if self._cursor_idx == i:
                    self._select_current()
                else:
                    self._cursor_idx = i
                    self.update()
                return


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, config: Config, history: History, initial_file=None):
        super().__init__()
        self.setWindowTitle("ScrollReader")
        self.resize(1100, 820)
        if config.get("start_fullscreen") in (True, "true", "True", "1", None):
            QTimer.singleShot(0, self.showFullScreen)
        self.setStyleSheet(f"background: {config.get('background_color')};")
        self.reader = ReaderWidget(config, history)
        self.setCentralWidget(self.reader)
        self.reader.setFocus()

        self.library = LibraryWidget(config, history, parent=self)
        self.library.hide()
        self.library.open_book = self.reader.load_document

        self.gamepad = GamepadManager(self, config)
        self.reader.gamepad_ref = self.gamepad
        if HAS_PYGAME:
            self._gp_timer = QTimer(self)
            self._gp_timer.timeout.connect(self.gamepad.poll)
            self._gp_timer.start(16)

        self._vu_timer = QTimer(self)
        self._vu_timer.timeout.connect(self._vu_tick)
        self._vu_timer.start(80)

        load_path = initial_file
        if not load_path and config.get("reopen_last"):
            last = history.last_file()
            if last and os.path.exists(last): load_path = last
        if load_path:
            QTimer.singleShot(80, lambda: self.reader.load_document(load_path))

    def _vu_tick(self):
        if getattr(self.reader,'_vu_active',False) or self.reader._render_thread is not None:
            self.reader.update()
        if self.library.isVisible() and getattr(self.library,'_lib_search_scanning',False):
            self.library.update()

    def show_library(self):
        try:
            cw = self.centralWidget()
            tl = cw.mapTo(self, cw.rect().topLeft())
            self.library.setGeometry(tl.x(), tl.y(), cw.width(), cw.height())
            self.library._refresh_books()
            self.library.show()
            self.library.raise_()
            self.library.setFocus()
        except Exception as ex:
            self.reader.status_text = f"library error: {ex}"
            self.reader.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if k == Qt.Key.Key_F11:
            if self.isFullScreen(): self.showMaximized()
            else: self.showFullScreen()
            return
        if not ctrl and not self.reader.command_mode:
            if k == Qt.Key.Key_R:
                if self.library.isVisible():
                    self.library.hide()
                    self.reader.setFocus()
                if self.reader._panel_mode:
                    self.reader._panel_back()
                return
            if k == Qt.Key.Key_L:
                if self.library.isVisible():
                    self.library.hide()
                    self.reader.setFocus()
                else:
                    self.show_library()
                return
            if k == Qt.Key.Key_N:
                if self.library.isVisible():
                    self.library.hide()
                    self.reader.setFocus()
                self.reader._open_annot_panel("notes")
                return
            if k == Qt.Key.Key_B:
                if self.library.isVisible():
                    self.library.hide()
                    self.reader.setFocus()
                self.reader._open_annot_panel("bookmarks")
                return
            if k == Qt.Key.Key_H:
                if self.library.isVisible():
                    self.library.hide()
                    self.reader.setFocus()
                self.reader._open_annot_panel("highlights")
                return
            if k == Qt.Key.Key_J:
                if self.library.isVisible():
                    self.library.hide()
                    self.reader.setFocus()
                if self.reader._panel_mode:
                    self.reader._panel_back()
                self.reader._open_search_panel()
                return
        if ctrl:
            if k == Qt.Key.Key_M:
                self.reader._cycle_swatch(1)
                if self.library.isVisible(): self.library.update()
                return
            if k == Qt.Key.Key_E:
                self.reader._cycle_font(-1)
                self.update(); return
            if k == Qt.Key.Key_R:
                self.reader._cycle_font(1)
                self.update(); return
        super().keyPressEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.library.isVisible():
            self.library.setGeometry(self.centralWidget().geometry())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ScrollReader")
    _load_vga_font()
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        bundled = os.path.join(meipass, "fonts")
        if os.path.exists(bundled):
            for f in os.listdir(bundled):
                if f.lower().endswith(('.ttf', '.otf', '.otb')):
                    QFontDatabase.addApplicationFont(os.path.join(bundled, f))
    config = Config()
    global TOP_BAR_H, BOTTOM_BAR_H, PANEL_W
    TOP_BAR_H    = int(config.get("top_bar_h")    or TOP_BAR_H)
    BOTTOM_BAR_H = int(config.get("bottom_bar_h") or BOTTOM_BAR_H)
    PANEL_W      = int(config.get("panel_w")      or PANEL_W)
    swatch = config.get("current_swatch") or "amber"
    _apply_swatch(swatch, config)
    _apply_theme(config)
    _UI_FONT_OFFSET_ref[0] = int(config.get("ui_font_offset") or 0)
    fonts = _scan_fonts()
    fidx  = int(config.get("current_font_idx") or 0)
    if fonts and fidx < len(fonts):
        _UI_FONT_FAMILY_ref[0] = _load_font_by_path(fonts[fidx]) or _UI_FONT_FAMILY_ref[0]
    history = History()
    initial = sys.argv[1] if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else None
    window = MainWindow(config, history, initial_file=initial)
    window.showMaximized()
    window.reader.setFocus()
    if not config.get("wizard_completed"):
        QTimer.singleShot(200, lambda: window.reader._open_wizard("app"))
    sys.exit(app.exec())

if __name__ == "__main__":
    main()    
