#!/usr/bin/env python3
"""
ScrollReader — focused PDF reader with line-by-line navigation.

Controls:
  Space / Down           — next line
  Up / Backspace / Tab   — previous line
  Page Down / Up         — jump one screenful
  Scroll wheel           — navigate lines
  Enter                  — command mode
  Escape                 — close panel / cancel confirm / exit command mode

Annotation commands  (note: ;;text;; appends a note to any command)
  nl[#][;;note;;]             note at current or line #
  bl[#][;;note;;]             bookmark line
  bp[#][;;note;;]             bookmark page
  hl[fwd][;back][;;note;;]    highlight lines around current  (hl5 = fwd 5, hl5;3 = fwd5+back3)
  hp[fwd][;back][;;note;;]    highlight pages around current
  hl40-89[;;note;;]           highlight absolute line range

Navigation
  gl#   gp#   lb[#]   lf[#]   pb[#]   pf[#]

Remove  (all prompt y/n, accept optional ;;reason;;)
  rl[spec]   rp[spec]          remove all annotation types in range
  rb[spec]   rn[spec]   rh[spec]   remove specific type
  removeall                    remove all annotations for this book
  removeall+                   wipe all stored data for this book

  Range specs (same for remove and export):
    rl         current line
    rl40       line 40
    rl40-89    lines 40–89
    rl5;3      fwd 5, back 3 from current line
    rp / rp4 / rp4-10 / rp5;2   same but page-based

Export  (writes Markdown)
  e                     export all annotations
  el[spec]  ep[spec]    export by line / page range
  eb[spec]  en[spec]  eh[spec]   export specific type

Panels (Esc to close, click item to navigate)
  sn  showbookmarks  sb  showhighlights  sh

Zoom
  zoom fit-width / fit-page / 50% / 75% / 100% / cycle

Other
  open <path>           open a PDF
  bookinfo              show metadata + annotation counts
  setmeta <field> <v>   title / author / status / rating / tags
  bookset <key> <v>     per-book display override
  set <key> <v>         global config
  showconfig            show global config
  q / quit              exit
"""

import sys, json, os, re, time
from pathlib import Path
from collections import namedtuple
from typing import Optional, Callable

import fitz
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QLineEdit
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import (QPainter, QColor, QFont, QPixmap, QImage,
                          QKeyEvent, QPolygon, QWheelEvent, QMouseEvent,
                          QFontMetrics, QFontDatabase)

# ---------------------------------------------------------------------------
# Amber phosphor theme
# ---------------------------------------------------------------------------

AMBER         = QColor("#ffbb33")   # primary UI text / elements
AMBER_BRIGHT  = QColor("#ffb000")   # emphasis, indicator, active
AMBER_DIM     = QColor("#996600")   # secondary / labels
AMBER_DARK    = QColor("#553300")   # borders, separators
AMBER_VERY_DIM= QColor("#332200")   # very subtle borders
AMBER_INV_BG  = QColor("#ffbb33")   # inverse background (selected)
AMBER_INV_FG  = QColor("#000000")   # inverse foreground
UI_BG         = QColor("#000000")   # pure black background

# Amber swatch for library blocks — brightness variations
DEFAULT_SWATCH = [
    "#ffbb33", "#e69900", "#cc7700", "#b36200",
    "#ff9900", "#ffcc66", "#cc8800", "#aa5500",
    "#ff8800", "#d4a017",
]

_UI_FONT_FAMILY_ref = ["Courier New"]   # mutable ref — use _UI_FONT_FAMILY_ref[0]


def _app_dir() -> str:
    """Directory containing the exe (frozen) or the script (source)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _load_vga_font() -> str:
    """Try to load IBM VGA 8x16 font. Returns family name."""
    global _UI_FONT_FAMILY
    base     = _app_dir()
    meipass  = getattr(sys, '_MEIPASS', base)
    candidates = [
        os.path.join(base,    "fonts", "Px437_IBM_VGA_8x16.ttf"),
        os.path.join(base,    "fonts", "Px437_IBM_VGA-8x16.ttf"),
        os.path.join(base,    "fonts", "Web437_IBM_VGA_8x16.ttf"),
        os.path.join(meipass, "fonts", "Px437_IBM_VGA_8x16.ttf"),
        os.path.join(meipass, "fonts", "Px437_IBM_VGA-8x16.ttf"),
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


_UI_FONT_OFFSET_ref = [0]   # mutable so no 'global' needed in methods


def _ui_font(size: int = 10, bold: bool = False) -> QFont:
    f = QFont(_UI_FONT_FAMILY_ref[0], max(6, size + _UI_FONT_OFFSET_ref[0]))
    if bold:
        f.setWeight(QFont.Weight.Bold)
    return f


def _mk_pen(color: QColor, width: int = 1) -> 'QPen':
    from PyQt6.QtGui import QPen
    p = QPen(color)
    p.setWidth(width)
    return p


def _apply_theme(config):
    """Update global theme colors from config."""
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
    "green_lcd":       {"bg": "#ccff00", "primary": "#111111", "bright": "#000000",
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
_pdf_invert_ref     = [False]   # per-book, updated on load/toggle


def _apply_swatch(name: str, config):
    """Apply a named swatch to config and globals."""
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
    """Return list of TTF/OTF paths in fonts/ next to the exe or script."""
    fonts_dir = os.path.join(_app_dir(), "fonts")
    if not os.path.exists(fonts_dir):
        return []
    return sorted([os.path.join(fonts_dir, f)
                   for f in os.listdir(fonts_dir)
                   if f.lower().endswith(('.ttf', '.otf', '.otb'))])


def _load_font_by_path(path: str) -> str:
    """Load a font file and return its family name."""
    fid = QFontDatabase.addApplicationFont(path)
    if fid >= 0:
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            return families[0]
    return _UI_FONT_FAMILY_ref[0]




# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOP_BAR_H    = 28   # top info bar
BOTTOM_BAR_H = 28   # bottom controls/command bar
L_MARGIN     = 0    # no left margin
R_MARGIN     = 0    # no right margin (legacy, kept for compat)
PANEL_W      = 160  # single margin width (reading + panel mode)
BORDER_W     = 2    # default border width
APP_DIR      = Path.home() / ".scrollreader"
CONFIG_PATH  = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.json"

DEFAULT_CONFIG = {
    "reopen_last":           True,
    "midpoint":              0.42,
    "zoom_mode":             "fit-width",
    "zoom_fixed":            1.5,
    "indicator_color":       "#ffb000",   # amber bright
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
    "library_swatch":        [],            # empty = use built-in swatch
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
    "help_col_offset":       0,
    "library_flip_mode":     False,
    "eager_pages":           2,             # pages rendered synchronously each side of current
}

ZOOM_MODES = ["fit-width", "fit-page", "50%", "75%", "100%"]

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
# mode: 'current' | 'absolute' | 'relative' | 'error'


def parse_range_spec(spec: str, hl_style: bool = False) -> RangeSpec:
    """
    hl_style=True  → single number means forward N lines (hl/hp)
    hl_style=False → single number means absolute position (everything else)
    """
    if not spec:
        return RangeSpec(mode="current")
    # Absolute range N-M
    m = re.fullmatch(r"(\d+)-(\d+)", spec)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        if s > e:
            return RangeSpec(mode="error", error=f"invalid range {s}-{e}: start must be ≤ end")
        return RangeSpec(mode="absolute", start=s, end=e)
    # Relative fwd;back  (either side may be empty)
    m = re.fullmatch(r"(\d*);(\d*)", spec)
    if m:
        fwd  = int(m.group(1)) if m.group(1) else 0
        back = int(m.group(2)) if m.group(2) else 0
        return RangeSpec(mode="relative", fwd=fwd, back=back)
    # Single number
    m = re.fullmatch(r"(\d+)", spec)
    if m:
        n = int(m.group(1))
        if hl_style:
            return RangeSpec(mode="relative", fwd=n, back=0)
        else:
            return RangeSpec(mode="absolute", start=n, end=n)
    return RangeSpec(mode="error", error=f"unknown range: '{spec}'")


def _extract_note(text: str) -> tuple[str, Optional[str]]:
    """Strip trailing ;;note;; and return (base, note_or_None). Uses greedy match."""
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

    # Special exact matches
    if b == "removeall+":
        return {"cmd": "removeall_plus"}
    if b == "removeall":
        return {"cmd": "removeall", "reason": note}
    if b == "e":
        return {"cmd": "export_all"}

    # Simple navigation (no range system, just optional count)
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

    # Annotation creates
    for pat, cmd, hl in [
        (r"(nl|noteline)(" + RANGE_CHARS + r")",         "note",           False),
        (r"(bl|bookmarkline)(" + RANGE_CHARS + r")",     "bookmark_line",  False),
        (r"(bp|bookmarkpage)(" + RANGE_CHARS + r")",     "bookmark_page",  False),
        (r"(hl|highlightline)(" + RANGE_CHARS + r")",    "highlight_line", False),
        (r"(hp|highlightpage)(" + RANGE_CHARS + r")",    "highlight_page", False),
    ]:
        m = re.fullmatch(pat, b)
        if m:
            rs = parse_range_spec(m.group(2), hl_style=hl)
            return {"cmd": cmd, "range": rs, "note": note or ""}

    # Remove commands
    for pat, cmd in [
        (r"(rl|removeline)(" + RANGE_CHARS + r")",      "remove_line"),
        (r"(rp|removepage)(" + RANGE_CHARS + r")",      "remove_page"),
        (r"(rb|removebookmark)(" + RANGE_CHARS + r")",  "remove_bookmark"),
        (r"(rn|removenote)(" + RANGE_CHARS + r")",      "remove_note"),
        (r"(rh|removehighlight)(" + RANGE_CHARS + r")", "remove_highlight"),
    ]:
        m = re.fullmatch(pat, b)
        if m:
            rs = parse_range_spec(m.group(2), hl_style=False)
            return {"cmd": cmd, "range": rs, "reason": note}

    # Export commands
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

    # Print commands
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
        """Render a page with colors inverted."""
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = self.doc[pn].get_pixmap(matrix=mat, alpha=False)
        pix.invert_irect()   # C-level, very fast
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

    def __init__(self, document: PDFDocument, start_page: int,
                 preload_inv: bool = True):
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
        # Pass 1: normal render
        for pn in order:
            if self._cancel: return
            if self.document.page_pixmaps[pn] is None:
                try:
                    pm = self.document.render_page(pn)
                    self.document.page_pixmaps[pn] = pm
                    self.page_ready.emit(pn, pm)
                except Exception:
                    pass
        # Pass 2: inverted render (if enabled)
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
    """Pages in outward spiral from start: start, start+1, start-1, start+2 ..."""
    seen, order = set(), []
    for delta in range(total):
        for pn in [start + delta, start - delta]:
            if 0 <= pn < total and pn not in seen:
                seen.add(pn); order.append(pn)
    return order


# ---------------------------------------------------------------------------
# Reader Widget
# ---------------------------------------------------------------------------

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
        # New state
        self._line_history:  list[int] = []   # movement undo stack (max 50)
        self._panel_mode: Optional[str] = None  # 'bookmarks'/'notes'/'highlights'
        self._panel_cursor: int        = 0
        self._pre_panel_line: int      = 0
        self._search_term: str         = ''
        self._search_re:    Optional[re.Pattern] = None
        self._last_command: str        = ''

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(600, 400)
        self.setMouseTracking(False)

        self._cmd_cooldown = 0.0

        self.cmd = QLineEdit(self)
        self.cmd.setVisible(False)
        self.cmd.installEventFilter(self)
        self._update_cmd_style()

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
        """Update command bar style to match current theme and font size."""
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
        # Show dimensions while resizing
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
        self.update()   # repaint bottom bar as amber inverse

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

        # Cancel any in-progress render
        self._stop_render_thread()

        try:
            zoom = self._compute_zoom(self.zoom_mode, peek_path=filepath)
            doc  = PDFDocument(filepath, zoom=zoom, page_gap=int(self.config.get("page_gap")))

            # Recompute zoom now that we have exact natural dimensions
            # (handles fit-width/fit-page more accurately than the peek)
            zoom2 = self._compute_zoom_for_doc(self.zoom_mode, doc)
            if abs(zoom2 - zoom) > 0.01:
                doc = PDFDocument(filepath, zoom=zoom2, page_gap=int(self.config.get("page_gap")))

            # Metadata
            meta = doc.doc.metadata
            e    = self.history._entry(filepath)
            if meta.get("title")  and not e.get("title"):  e["title"]  = meta["title"]
            if meta.get("author") and not e.get("author"): e["author"] = meta["author"]
            self.history.set_totals(filepath, len(doc.lines), doc.page_count)

            self.document     = doc
            self.current_line = min(self.history.get_line(filepath), max(0, len(doc.lines)-1))
            self.panel        = None
            self._pending     = None

            # Restore per-book invert state
            _pdf_invert_ref[0] = bool(
                self.history._entry(filepath).get("pdf_invert", False))

            # Synchronously render pages around current position
            cur_page  = doc.lines[self.current_line].page_num if doc.lines else 0
            eager     = int(self.config.get("eager_pages") or 2)
            preload   = bool(self.config.get("preload_inverted") if self.config.get("preload_inverted") is not None else True)
            doc.render_range(cur_page - eager, cur_page + eager,
                             inverted=preload)

            self.update()

            # Background-render the rest
            self._start_render_thread(cur_page)

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
        old = self.current_line
        self.update(); QApplication.processEvents()
        try:
            self.load_document(self.document.filepath)
            self.current_line = min(old, max(0, len(self.document.lines)-1))
        except Exception as ex:
            pass
        self.update()

    # -------------------------------------------------------------- helpers

    def _margin_side(self) -> str:
        return str(self.config.get("margin_side") or "right")

    def _bw(self) -> int:
        return max(1, int(self.config.get("ui_border_width") or 2))

    def _vp(self) -> QRect:
        """PDF viewport — full window minus the single margin and bars."""
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
        """METAR string for current document."""
        if not self.document: return ""
        e     = self.history._entry(self.document.filepath)
        total = len(self.document.lines)
        cur   = self.current_line
        pct   = int(cur / max(total, 1) * 100)
        n  = len(e.get("notes", []))
        b  = len(e.get("bookmarks", []))
        h  = len(e.get("highlights", []))
        return f"N{n}B{b}H{h}R{pct}P{len(self.document.doc)}"

    def _update_status(self):
        pass   # status now lives in top bar, no separate status_text needed

    def _push_history(self, line: int):
        """Push a line to movement history (max 50)."""
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

    # -------------------------------------------------------------- paint

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Black background
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
            return

        scroll = self._scroll_offset()
        px     = self._page_x_offset()
        lh     = self._lh()
        voff   = self._voff()
        vp     = self._vp()
        dlines = self.document.lines
        total  = len(dlines)

        # ── Pages ─────────────────────────────────────────────────────────
        inv = _pdf_invert_ref[0]
        for i in range(self.document.page_count):
            py = vp.y() + self.document.page_offsets[i] - scroll
            pw, ph = self.document.page_sizes[i]
            if py + ph >= vp.y() and py <= vp.bottom():
                painter.drawPixmap(px, int(py), self.document.get_pixmap(i, inverted=inv))

        # ── Saved highlights (on PDF) ──────────────────────────────────────
        sh_col = QColor(self._cfg("saved_highlight_color") or AMBER.name())
        sh_col.setAlpha(int(self._cfg("saved_highlight_alpha") or 30))
        for hh in self.history._entry(self.document.filepath).get("highlights", []):
            sl, el = hh.get("start_line",0), hh.get("end_line",0)
            if sl >= total or el >= total: continue
            sy = int(vp.y() + dlines[sl].abs_y - scroll + voff)
            ey = int(vp.y() + dlines[el].abs_y - scroll + voff + lh)
            if ey >= vp.y() and sy <= vp.bottom():
                painter.fillRect(QRect(px, sy, self.document.max_width, ey-sy), sh_col)

        # ── Current-line highlight band ────────────────────────────────────
        ind_y   = self._indicator_screen_y() + voff
        ind_col = QColor(self._cfg("indicator_color") or "#ffb000")
        ind_col.setAlpha(int(self._cfg("highlight_alpha") or 35))
        painter.fillRect(QRect(px, ind_y-2, self.document.max_width, lh), ind_col)

        # ── Margins ────────────────────────────────────────────────────────
        self._paint_margin(painter, scroll, voff, vp, dlines, total, lh, ind_y)

        # ── Frame & bars (on top of everything) ───────────────────────────
        self._paint_frame(painter)
        self._paint_top_bar(painter)
        self._paint_bottom_bar(painter)

        # ── Overlays ──────────────────────────────────────────────────────
        if self.panel and self.panel.get("kind") == "help":
            self._paint_help_panel(painter)
        if self._pending:
            self._paint_confirm(painter)

    # ── Frame & bars ──────────────────────────────────────────────────────

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
        # Bottom separator
        painter.setPen(AMBER_DARK)
        painter.setPen(_mk_pen(AMBER_DARK, self._bw())); painter.drawLine(0, TOP_BAR_H-1, w, TOP_BAR_H-1)

        font_b = _ui_font(9, bold=True)
        font   = _ui_font(9)
        ty     = TOP_BAR_H - 8

        if self.document and self.document.lines:
            line  = self.document.lines[self.current_line]
            total = len(self.document.lines)
            # Left: LINE ##/### PAGE ##/### METAR
            left_txt = (f"LINE {self.current_line+1}/{total}"
                        f"  PAGE {line.page_num+1}/{self.document.page_count}"
                        f"  {self._metar()}")
            painter.setPen(AMBER)
            painter.setFont(font_b)
            painter.drawText(L_MARGIN + 6, ty, left_txt)

            # Centre: TITLE, AUTHOR
            e    = self.history._entry(self.document.filepath)
            title  = (e.get("title") or Path(self.document.filepath).stem)[:28]
            author = (e.get("author") or "")[:20]
            centre_txt = f"{title}{',  '+author if author else ''}"
            painter.setPen(AMBER_BRIGHT)
            painter.setFont(font_b)
            fm  = QFontMetrics(font_b)
            cx  = (w - fm.horizontalAdvance(centre_txt)) // 2
            painter.drawText(cx, ty, centre_txt)

            # Right: MODE
            mode_map = {None: "READING", "bookmarks": "BOOKMARK VIEW",
                        "notes": "NOTE VIEW", "highlights": "HIGHLIGHT VIEW"}
            mode_txt = mode_map.get(self._panel_mode, "READING") + " MODE"
            painter.setPen(AMBER_DIM)
            painter.setFont(font)
            painter.drawText(w - PANEL_W - fm.horizontalAdvance(mode_txt) - 8
                             if self._margin_side() == "right"
                             else PANEL_W + 8, ty, mode_txt)
        else:
            painter.setPen(AMBER_DIM)
            painter.setFont(font_b)
            painter.drawText(L_MARGIN + 6, ty, "SCROLLREADER")

    def _paint_bottom_bar(self, painter: QPainter):
        w, h = self.width(), self.height()
        y    = h - BOTTOM_BAR_H

        if self.command_mode:
            # Amber inverse background — cmd QLineEdit widget paints on top
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
                       "L LIB   N/B/H PANELS   I INVERT   ?  HELP   "
                       "gg TOP   G BOTTOM   CTRL+K/L SWATCH   CTRL+O/P IND.COLOR   "
                       "SN/SP/SF/SL SEARCH   CC REPEAT   = UNDO")
            painter.drawText(6, h - 8, ref)

    # ── Margin indicators ─────────────────────────────────────────────────

    def _margin_indicator_y(self, line_idx: int, scroll: float, voff: int, vp: QRect) -> int:
        if not self.document or line_idx >= len(self.document.lines):
            return -999
        return int(vp.y() + self.document.lines[line_idx].abs_y - scroll + voff)

    # ── Single margin (reading indicators + panel mode) ───────────────────

    def _margin_rect(self) -> QRect:
        side = self._margin_side()
        x = 0 if side == "right" else 0
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

        # Background
        painter.fillRect(mr, UI_BG)

        # Border line (on the PDF side)
        painter.setPen(_mk_pen(AMBER_DARK, self._bw()))
        if side == "right":
            painter.drawLine(mr.left(), mr.top(), mr.left(), mr.bottom())
        else:
            painter.drawLine(mr.right(), mr.top(), mr.right(), mr.bottom())

        if self._panel_mode:
            self._paint_panel_list(painter, mr, e, lh)
        else:
            self._paint_margin_reading(painter, mr, scroll, voff, vp,
                                        dlines, total, lh, ind_y, e, side)

    def _paint_margin_reading(self, painter, mr, scroll, voff, vp,
                               dlines, total, lh, ind_y, e, side):
        """Reading mode: small indicators + annotation text."""
        IND_W   = 12
        TEXT_X  = mr.left() + IND_W + 6 if side == "right" else mr.left() + 6
        TEXT_W  = PANEL_W - IND_W - 10
        font    = _ui_font(8)
        fm      = QFontMetrics(font)
        painter.setFont(font)
        ind_color = QColor(self._cfg("indicator_color") or "#ffb000")

        # Current line indicator triangle
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

        # Collect all visible annotations sorted by Y position
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

        # Annotation text — word-wrap notes, truncate bookmarks/highlights
        items.sort(key=lambda x: x[1])
        last_y = -999
        for kind, ay, line, note in items:
            # Small indicator symbol
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

            # Note text — word wrap within available space
            if note and ay > last_y + 4:
                painter.setPen(AMBER_DIM)
                painter.setFont(font)
                if kind == "note":
                    # Word-wrap for notes
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

        # Highlight bars (thin vertical strip)
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
        """Panel mode: scrollable annotation list."""
        items    = self._panel_items()
        font_b   = _ui_font(9, bold=True)
        font     = _ui_font(9)
        pw       = mr.width()
        px       = mr.left()
        py       = mr.top() + 4

        # Mode label + separator — draw separator BELOW the label with enough gap
        painter.setPen(AMBER_DIM)
        painter.setFont(font_b)
        mode_label = (self._panel_mode or "").upper()
        painter.drawText(px+8, py+16, mode_label)
        py += 22
        painter.setPen(_mk_pen(AMBER_VERY_DIM, self._bw()))
        painter.drawLine(px+4, py, px+pw-4, py)
        py += 6

        if not items:
            painter.setPen(AMBER_DARK)
            painter.setFont(font)
            painter.drawText(QRect(px, py, pw, 40),
                             Qt.AlignmentFlag.AlignCenter, "(none)")
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

            # Calculate how many lines the note takes
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

            # Find next item's Y to know how much room we have
            item_h = 20 + len(note_lines) * lh_line + 6

            if py + item_h > mr.bottom() - 4: break

            if selected:
                painter.fillRect(QRect(px+2, py, pw-4, item_h), AMBER_INV_BG)
                fg = AMBER_INV_FG; fg2 = AMBER_INV_FG
            else:
                fg = AMBER; fg2 = AMBER_DIM

            # Bigger location indicator
            painter.setPen(fg); painter.setFont(font_b)
            painter.drawText(px+8, py+16, loc_str)

            # Wrapped note text
            if note_lines:
                painter.setFont(font); painter.setPen(fg2)
                ty = py + 20
                for line in note_lines:
                    painter.drawText(px+8, ty + lh_line - 2, line)
                    ty += lh_line

            painter.setPen(_mk_pen(AMBER_VERY_DIM, self._bw()))
            painter.drawLine(px+4, py+item_h, px+pw-4, py+item_h)
            py += item_h

    def _cycle_indicator_color(self, delta_deg: int):
        """Shift the indicator (main line) color by delta_deg on the HSV hue wheel."""
        cur = QColor(self.config.get("indicator_color") or "#ffb000")
        h, s, v, _ = cur.getHsvF()
        new_h = (h + delta_deg / 360.0) % 1.0
        new_color = QColor.fromHsvF(new_h, max(0.6, s), max(0.7, v))
        hex_color = new_color.name()
        self.config.set("indicator_color", hex_color)
        self.config.data["indicator_color"] = hex_color  # ensure immediate effect
        self.status_text = f"indicator: {hex_color}"
        QTimer.singleShot(3000, self._clear_status)
        self.update()

    def _open_annot_panel(self, mode: str) -> Optional[str]:
        if not self.document: return "no document open"
        # Toggle: calling same mode again closes panel
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

    def _paint_statusbar(self, painter):
        pass   # replaced by _paint_top_bar / _paint_bottom_bar

    def _paint_panel(self, painter):
        self._panel_rects = []
        pw   = max(340, self.width()//2)
        px   = self.width() - pw
        py   = TOP_BAR_H
        ph   = self.height() - TOP_BAR_H

        painter.fillRect(QRect(px, py, pw, ph), QColor(0,0,0,230))
        painter.setPen(_mk_pen(AMBER_DARK, 2))
        painter.drawLine(px, py, px, self.height())

        painter.setPen(AMBER_DIM)
        painter.setFont(_ui_font(11, bold=True))
        painter.drawText(px+14, py+22, self.panel["title"])
        painter.setPen(_mk_pen(AMBER_VERY_DIM, 2))
        painter.drawLine(px, py+30, self.width(), py+30)

        items  = self.panel.get("items", [])
        kind   = self.panel.get("kind", "")
        y      = py + 50
        row_h  = 38

        painter.setFont(_ui_font(10))
        if not items:
            painter.setPen(AMBER_DARK)
            painter.drawText(px+14, y+14, "(none)")
        else:
            for item in items:
                if y + row_h > self.height() - 20:
                    painter.setPen(AMBER_DARK)
                    remaining = len(items) - items.index(item)
                    painter.drawText(px+14, y+14, f"… {remaining} more")
                    break

                if kind == "notes":
                    loc  = f"line {item.get('line',0)+1}"
                    body = item.get("note","")
                    nav_line = item.get("line", 0)
                elif kind == "bookmarks":
                    loc  = f"line {item.get('line',0)+1}  (page {item.get('page',0)+1})"
                    body = item.get("note","")
                    nav_line = item.get("line", 0)
                else:
                    sl, el = item.get("start_line",0), item.get("end_line",0)
                    loc    = f"lines {sl+1}–{el+1}"
                    body   = item.get("note","")
                    nav_line = sl

                rect = QRect(px, y, pw, row_h)
                self._panel_rects.append((rect, nav_line))

                painter.setPen(AMBER_DIM)
                painter.drawText(px+14, y+14, loc)
                if body:
                    metrics = painter.fontMetrics()
                    painter.setPen(AMBER)
                    painter.drawText(px+14, y+28, metrics.elidedText(body, Qt.TextElideMode.ElideRight, pw-30))

                painter.setPen(QColor("#222222"))
                painter.drawLine(px+8, y+row_h, self.width()-8, y+row_h)
                y += row_h

        painter.setPen(AMBER_DARK)
        painter.setFont(_ui_font(9))
        painter.drawText(px+14, self.height()-8, "click to jump  —  Esc to close")

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
        ("OTHER", None, [
            ("help  /  man  /  ?",  "Show this help panel"),
            ("q  /  quit  /  exit", "Quit ScrollReader"),
        ]),
    ]

    def _paint_help_panel(self, painter: QPainter):
        pw = self.width()
        py = TOP_BAR_H
        ph = self.height() - TOP_BAR_H

        # Background
        painter.fillRect(QRect(0, py, pw, ph), QColor(0,0,0,252))

        # Clip to panel area
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
            # Section header
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
                    # Continuation / descriptive line
                    painter.setPen(AMBER_DIM)
                    painter.setFont(mono)
                    # Word-wrap simple approach: draw in full available width
                    painter.drawText(margin, y + lh_body - 4, val)
                    y += lh_body
                else:
                    # Key column (left, amber)
                    painter.setPen(AMBER)
                    painter.setFont(mono_b)
                    painter.drawText(margin, y + lh_body - 4, key)
                    if val:
                        # Value column (right, grey)
                        painter.setPen(AMBER_DIM)
                        painter.setFont(mono)
                        painter.drawText(col2_x + margin, y + lh_body - 4, val)
                    y += lh_body

            y += 6  # section gap

        painter.setClipping(False)

        # Footer bar
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

        # ── Confirmation overlay ──────────────────────────────────────────
        if self._pending:
            if k == Qt.Key.Key_Y:
                result = self._pending["action"]()
                self._pending = None; self.update()
            elif k in (Qt.Key.Key_N, Qt.Key.Key_Escape, Qt.Key.Key_Tab):
                self._pending = None; self.update()
            return

        # ── Annotation panel navigation ───────────────────────────────────
        if self._panel_mode:
            if k in (Qt.Key.Key_Tab, Qt.Key.Key_Escape):
                self._panel_back()
            elif k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._panel_select()
            elif k in (Qt.Key.Key_Down, Qt.Key.Key_Right,
                       Qt.Key.Key_S, Qt.Key.Key_D):
                self._panel_navigate(1)
            elif k in (Qt.Key.Key_Up, Qt.Key.Key_Left,
                       Qt.Key.Key_W, Qt.Key.Key_A):
                self._panel_navigate(-1)
            if ctrl and k == Qt.Key.Key_Return:
                if time.time() > self._cmd_cooldown:
                    self._enter_command_mode()
            return

        # ── Help/overlay panel ────────────────────────────────────────────
        if self.panel:
            if k in (Qt.Key.Key_Escape, Qt.Key.Key_Tab):
                self.panel = None; self._panel_scroll = 0; self.update()
            elif k in (Qt.Key.Key_Down, Qt.Key.Key_Space):
                self._panel_scroll += 40; self.update()
            elif k == Qt.Key.Key_Up:
                self._panel_scroll = max(0, self._panel_scroll - 40); self.update()
            elif k == Qt.Key.Key_PageDown:
                self._panel_scroll += self.height() - TOP_BAR_H - 60; self.update()
            elif k == Qt.Key.Key_PageUp:
                self._panel_scroll = max(0, self._panel_scroll - (self.height() - TOP_BAR_H - 60)); self.update()
            return

        if self.command_mode:
            if k == Qt.Key.Key_Escape: self._exit_command_mode()
            return

        # ── Ctrl shortcuts ────────────────────────────────────────────────
        if ctrl:
            if k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._enter_command_mode(); return
            if k == Qt.Key.Key_K:
                self._cycle_swatch(-1); return
            if k == Qt.Key.Key_L:
                self._cycle_swatch(1); return
            if k == Qt.Key.Key_O:
                self._cycle_indicator_color(-5); return
            if k == Qt.Key.Key_P:
                self._cycle_indicator_color(5); return
            if k == Qt.Key.Key_BracketLeft:
                cur = int(self._cfg("highlight_height") or 20)
                self.config.set("highlight_height", str(max(4, cur - 2)))
                self.update(); return
            if k == Qt.Key.Key_BracketRight:
                cur = int(self._cfg("highlight_height") or 20)
                self.config.set("highlight_height", str(cur + 2))
                self.update(); return
            if k == Qt.Key.Key_Equal:
                cur = int(self.config.get("ui_border_width") or 2)
                self.config.set("ui_border_width", str(cur + 1))
                self.update(); return
            if k == Qt.Key.Key_Minus:
                cur = int(self.config.get("ui_border_width") or 2)
                self.config.set("ui_border_width", str(max(1, cur - 1)))
                self.update(); return
            if k == Qt.Key.Key_Semicolon:
                _UI_FONT_OFFSET_ref[0] += 1
                self.config.set("ui_font_offset", str(_UI_FONT_OFFSET_ref[0]))
                self._update_cmd_style(); self.update(); return
            if k == Qt.Key.Key_Apostrophe:
                _UI_FONT_OFFSET_ref[0] = max(-6, _UI_FONT_OFFSET_ref[0] - 1)
                self.config.set("ui_font_offset", str(_UI_FONT_OFFSET_ref[0]))
                self._update_cmd_style(); self.update(); return
            if k == Qt.Key.Key_Comma:
                self._cycle_font(-1); return
            if k == Qt.Key.Key_Period:
                self._cycle_font(1); return
            if k == Qt.Key.Key_E:
                cur = int(self.config.get("help_col_offset") or 0)
                self.config.set("help_col_offset", str(cur - 20))
                self.update(); return
            if k == Qt.Key.Key_R:
                cur = int(self.config.get("help_col_offset") or 0)
                self.config.set("help_col_offset", str(cur + 20))
                self.update(); return
            if k == Qt.Key.Key_F:
                flip = not bool(self.config.get("library_flip_mode"))
                self.config.set("library_flip_mode", flip)
                self.status_text = f"library: {'pages read' if flip else 'pages remaining'}"
                QTimer.singleShot(5000, self._clear_status)
                self.update(); return
            return  # eat other unhandled Ctrl combos

        # F11 fullscreen (no modifier needed)
        if k == Qt.Key.Key_F11:
            w = self.window()
            if w.isFullScreen(): w.showMaximized()
            else:                w.showFullScreen()
            return

        # ── Normal reading keys ───────────────────────────────────────────
        if k == Qt.Key.Key_F11:
            w = self.window()
            if w.isFullScreen(): w.showMaximized()
            else: w.showFullScreen()
            return
        if k in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                 Qt.Key.Key_Enter, Qt.Key.Key_Down,
                 Qt.Key.Key_S):                          self._step(1)
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Backspace,
                   Qt.Key.Key_Tab, Qt.Key.Key_W):        self._step(-1)
        elif k in (Qt.Key.Key_PageDown, Qt.Key.Key_Right,
                   Qt.Key.Key_D):   self._step(self._lines_per_screen())
        elif k in (Qt.Key.Key_PageUp, Qt.Key.Key_Left,
                   Qt.Key.Key_A):   self._step(-self._lines_per_screen())
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
            # Undo last movement
            prev = self._pop_history()
            if prev is not None and self.document:
                self.current_line = max(0, min(len(self.document.lines)-1, prev))
                self.update()
        elif k == Qt.Key.Key_Question:
            # Help panel
            self._open_panel("ScrollReader — Command Reference", "help")
        elif k == Qt.Key.Key_G:
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # G = bottom of document
                if self.document and self.document.lines:
                    self._push_history(self.current_line)
                    self.current_line = len(self.document.lines) - 1
                    self.history.set_line(self.document.filepath, self.current_line)
                    self.update()
            else:
                # g — wait for second g (gg = top)
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
        elif k == Qt.Key.Key_R:
            # Return to reading mode — close any open panel (no-op if already reading)
            if self._panel_mode:
                self._panel_back()

    def wheelEvent(self, ev: QWheelEvent):
        delta = ev.angleDelta().y()
        if not delta: return
        if self._panel_mode:
            self._panel_navigate(-1 if delta < 0 else 1)
        elif self.panel:
            self._panel_scroll = max(0, self._panel_scroll - (delta // 3))
            self.update()
        elif not self._pending:
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

    # --------------------------------------------------------- navigation

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

    # ------------------------------------------------------- command mode

    def _execute_command(self):
        raw = self.cmd.text().lstrip(":").strip()
        self._exit_command_mode()
        if not raw: return
        # Save to history (avoid consecutive duplicates)
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
        """Cycle through fonts in the fonts/ directory."""
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
        """Cycle through handmade swatches."""
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

    def _clear_status(self):
        self.status_text = ""
        self.update()

    # Command aliases — short forms expand to full commands before processing
    CMD_ALIASES = {
        "bs":   "bookset",
        "setm": "setmeta",
        "bi":   "bookinfo",
        "sc":   "showconfig",
        "ms":   "ms",
        "vb":   "vb",
        "vn":   "vn",
        "vh":   "vh",
        "hlh":  "bookset highlight_height",
        "hla":  "bookset highlight_alpha",
        "hlo":  "bookset highlight_offset",
        "hlc":  "bookset indicator_color",
        "shc":  "bookset saved_highlight_color",
        "sha":  "bookset saved_highlight_alpha",
        "bmc":  "bookset bookmark_color",
        "ntc":  "bookset note_color",
        "lib":  "lib",
    }

    def _run(self, text: str) -> Optional[str]:
        parsed = parse_shortcut(text)
        if parsed:
            return self._exec_shortcut(parsed)

        # Expand aliases — match on first token or first two tokens
        parts = text.split(None, 2)
        if not parts: return None

        # Check two-token aliases first (e.g. "hlh 20" → "bookset highlight_height 20")
        two  = " ".join(parts[:2]).lower() if len(parts) >= 2 else ""
        one  = parts[0].lower()
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
            self.load_document(" ".join(parts[1:])); return None
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
        if cmd in ("help","man","?"):        return self._open_panel("ScrollReader — Command Reference", "help")

        # Search commands
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
        if cmd == "help":
            return ("nav: gl# gp# lb[#] lf[#] pb[#] pf[#]  |  "
                    "annotate: nl bl bp hl hp  |  remove: rl rp rb rn rh removeall removeall+  |  "
                    "export: e el ep eb en eh  |  panels: sn sb sh  |  "
                    "zoom  bookinfo  setmeta  bookset  set  q  — all ranges: N  N-M  fwd;back")
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

    def _open_panel(self, title, kind) -> Optional[str]:
        if kind != "help" and not self.document: return "no document open"
        self._panel_scroll = 0
        if kind == "help":
            self.panel = {"title": title, "kind": "help"}
        else:
            self.panel = {"title": title, "kind": kind,
                          "items": self.history._entry(self.document.filepath).get(kind, [])}
        self.update(); return None

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

        if direction == "sf":   # first
            indices = range(total)
        elif direction == "sl": # last
            indices = range(total-1, -1, -1)
        elif direction == "sn": # next (forward from cur+1)
            indices = list(range(cur+1, total)) + list(range(0, cur))
        elif direction == "sp": # previous (backward from cur-1)
            indices = list(range(cur-1, -1, -1)) + list(range(total-1, cur, -1))
        else:
            indices = range(total)

        wrapped = False
        for i in indices:
            if pattern.search(lines[i].text):
                if direction in ("sn", "sp"):
                    # Detect if we wrapped around
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

    # ------------------------------------------------- shortcut execution

    def _exec_shortcut(self, p: dict) -> Optional[str]:
        if not self.document and p["cmd"] not in ("removeall_plus",):
            if not self.document: return "no document open"

        doc   = self.document
        lines = doc.lines if doc else []
        total = len(lines)
        cur   = self.current_line
        cur_p = lines[cur].page_num if lines else 0
        cmd   = p["cmd"]

        # ── Navigation ────────────────────────────────────────────────────
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

        # ── Annotation creates ────────────────────────────────────────────
        if cmd == "note":
            rs = p["range"]
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, _ = self._resolve_line_range(rs)
            return self.history.add_note(doc.filepath, sl, p.get("note",""))

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

        # ── Removes ───────────────────────────────────────────────────────
        if cmd == "removeall_plus":
            if not self.document: return "no document open"
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
                    ["notes"]     if cmd == "remove_note"     else ["highlights"]
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

        # ── Exports ───────────────────────────────────────────────────────
        if cmd in ("export_all", "export_line", "export_page",
                   "export_bookmark", "export_note", "export_highlight"):
            return self._do_export(cmd, p)

        # ── Print ─────────────────────────────────────────────────────────
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

        # Determine kinds
        kinds = ALL_ANNOTATION_KINDS if cmd in ("export_all","export_line","export_page") else \
                ["bookmarks"]  if cmd == "export_bookmark"  else \
                ["notes"]      if cmd == "export_note"      else ["highlights"]

        # Determine line range
        if cmd == "export_all":
            items = {k: e.get(k,[]) for k in kinds}
            subtitle = "All annotations"
        elif cmd in ("export_line", "export_bookmark", "export_note", "export_highlight"):
            rs = p.get("range", RangeSpec("current"))
            if rs.mode == "error": return f"range error: {rs.error}"
            sl, el = self._resolve_line_range(rs)
            items = self.history.collect_in_range(fp, sl, el, kinds)
            subtitle = f"Lines {sl+1}–{el+1}" if sl != el else f"Line {sl+1}"
        else:  # export_page
            rs = p.get("range", RangeSpec("current"))
            if rs.mode == "error": return f"range error: {rs.error}"
            sp, ep = self._resolve_page_range(rs)
            sl, el = self._page_range_to_line_range(sp, ep)
            items = self.history.collect_in_range(fp, sl, el, kinds)
            subtitle = f"Pages {sp+1}–{ep+1}" if sp != ep else f"Page {sp+1}"

        count = sum(len(v) for v in items.values())
        if count == 0: return "nothing to export in that range"

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
            # Honour page range from dialog if the user set one
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

# Library Widget
# ---------------------------------------------------------------------------

LIBRARY_TABS_ROW1 = ["EDIT META", "SEARCH", "FAVORITES", "SETTINGS"]
LIBRARY_TABS_ROW2 = ["READING", "READ", "UNREAD", "ABANDONED"]
LIBRARY_TABS = LIBRARY_TABS_ROW1 + LIBRARY_TABS_ROW2
TAB_H          = 38
TAG_BAR_H      = 36
LIB_STATUS_H   = 24

STATUS_SAT = {
    "reading":   1.0,
    "unread":    0.65,
    "favorites": 1.0,
    "abandoned": 0.15,
    "read":      0.8,
}


def _adjust_sat(hex_color: str, factor: float) -> QColor:
    c = QColor(hex_color)
    h, s, v, a = c.getHsvF()
    c.setHsvF(h, min(1.0, s * factor), v, a)
    return c


def _book_color(filepath: str, config) -> str:
    """Derive a per-book color from the current theme primary hue + book hash."""
    # Get hue from current theme primary
    base = QColor(config.get("theme_primary") or "#ffbb33")
    h, s, v, _ = base.getHsvF()
    # Spread hue slightly per book using hash
    slot   = hash(filepath) % 12
    hshift = (slot - 6) / 60.0   # ±6 slots = ±0.1 hue shift
    vh     = (h + hshift) % 1.0
    # Vary value slightly too
    vv = max(0.3, min(1.0, v - 0.05 + (slot % 4) * 0.05))
    return QColor.fromHsvF(vh, s * 0.85, vv).name()


def _scan_pdfs(directory: str, recursive: bool = False) -> list[str]:
    p = Path(directory)
    if not p.exists():
        return []
    if recursive:
        return [str(f) for f in p.rglob("*.pdf")]
    else:
        return [str(f) for f in p.glob("*.pdf")]


def _default_lib_dir() -> str:
    """Default library dir: next to exe on Windows, ~/library on Linux."""
    if getattr(sys, 'frozen', False):
        # Running as exe
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    if sys.platform.startswith("win"):
        return base
    return str(Path.home() / "library")


def _squarify(books, weights, x, y, w, h, padding, min_w, min_h):
    """
    Slice-and-dice treemap. Largest items first, alternating horizontal/vertical splits.
    Returns [(book, QRect), ...].
    """
    if not books or not weights or w <= 0 or h <= 0:
        return []

    # Sort largest first
    pairs = sorted(zip(books, weights), key=lambda p: p[1], reverse=True)
    books_s   = [p[0] for p in pairs]
    weights_s = [p[1] for p in pairs]
    total     = sum(weights_s)
    results   = []

    def _layout(items, wts, rx, ry, rw, rh, depth=0):
        if not items or rw < min_w or rh < min_h:
            return
        if len(items) == 1:
            results.append((items[0],
                            QRect(int(rx) + padding, int(ry) + padding,
                                  max(min_w, int(rw) - padding * 2),
                                  max(min_h, int(rh) - padding * 2))))
            return

        wt_total = sum(wts)

        # Split into two halves by weight (binary split)
        # Find split point closest to 50/50
        cumulative = 0
        split_idx  = 1
        best_diff  = float('inf')
        for i in range(1, len(wts)):
            cumulative += wts[i-1]
            diff = abs(cumulative / wt_total - 0.5)
            if diff < best_diff:
                best_diff = diff
                split_idx = i

        frac_a = sum(wts[:split_idx]) / wt_total
        frac_b = 1.0 - frac_a

        # Choose split direction: split along the long axis
        if rw >= rh:
            # Split horizontally
            wa = max(min_w, int(rw * frac_a))
            wb = max(min_w, int(rw * frac_b))
            # Adjust to fill exactly
            wb = max(min_w, rw - wa)
            _layout(items[:split_idx], wts[:split_idx], rx, ry, wa, rh, depth+1)
            _layout(items[split_idx:], wts[split_idx:], rx+wa, ry, wb, rh, depth+1)
        else:
            # Split vertically
            ha = max(min_h, int(rh * frac_a))
            hb = max(min_h, int(rh * frac_b))
            hb = max(min_h, rh - ha)
            _layout(items[:split_idx], wts[:split_idx], rx, ry, rw, ha, depth+1)
            _layout(items[split_idx:], wts[split_idx:], rx, ry+ha, rw, hb, depth+1)

    _layout(books_s, weights_s, x, y, w, h)
    return results


class LibraryWidget(QWidget):
    """
    Full-screen overlay library browser.
    Opened via 'lib' / 'library' command. Closed via Esc.
    Emits open_book(filepath) signal when a book is clicked.
    """
    open_book = pyqtSignal(str)

    def __init__(self, config: Config, history: History, parent=None):
        super().__init__(parent)
        self.config  = config
        self.history = history
        self.tab     = "READING"
        self.active_tag: Optional[str] = None
        self._book_rects: list         = []  # [(QRect, filepath_or_None)]
        self._tab_rects:  list         = []
        self._tag_rects:  list         = []
        self._overflow_rect: Optional[QRect] = None  # the +N more cell
        self._cursor_idx: int          = 0   # keyboard nav index into _book_rects
        self._overflow_stack: list     = []  # stack of book lists for drill-down
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._cmd_mode    = False
        self._cmd_cooldown = 0.0
        self.cmd = QLineEdit(self)
        self.cmd.setVisible(False)
        self.cmd.installEventFilter(self)
        self.cmd.setStyleSheet("""
            QLineEdit {
                background-color: #000000; color: #ffbb33;
                border: none; border-top: 1px solid #553300;
                padding: 3px 10px;
                font-family: "IBM VGA 8x16", "Courier New", monospace; font-size: 13px;
                selection-background-color: #ffbb33; selection-color: #000000;
            }
        """)
        self.status_msg = ""

    def resizeEvent(self, ev):
        self.cmd.setGeometry(0, self.height() - 26, self.width(), 26)
        super().resizeEvent(ev)

    def showEvent(self, ev):
        self._overflow_stack = []
        self._cursor_idx     = 0
        self._refresh_books()
        self.setFocus()
        super().showEvent(ev)

    # ---------------------------------------------------------------- data

    def _refresh_books(self):
        """Scan library dir and merge with history, peeking page counts for new PDFs."""
        lib_dir   = self.config.get("library_dir") or _default_lib_dir()
        recursive = bool(self.config.get("library_recursive") or False)
        found     = _scan_pdfs(lib_dir, recursive)
        changed   = False
        for fp in found:
            e = self.history._entry(fp)
            if not e.get("total_pages"):
                try:
                    doc = fitz.open(fp)
                    n   = len(doc)
                    doc.close()
                    e["total_pages"] = n
                    changed = True
                except Exception:
                    e["total_pages"] = 1
        if changed:
            self.history._save()

    def _all_books(self) -> list[dict]:
        """Return list of enriched book dicts from history."""
        books = []
        for fp, e in self.history.data.items():
            if not os.path.exists(fp):
                continue
            books.append({
                "filepath":  fp,
                "title":     e.get("title") or Path(fp).stem,
                "author":    e.get("author") or "",
                "status":    e.get("status") or "unread",
                "rating":    e.get("rating") or 0,
                "tags":      e.get("tags") or [],
                "favorite":  bool(e.get("favorite")),
                "line":      e.get("line") or 0,
                "total":     e.get("total_lines") or (e.get("total_pages") or 1) * 40,
                "total_pages": e.get("total_pages") or 1,
                "notes":     len(e.get("notes", [])),
                "bookmarks": len(e.get("bookmarks", [])),
                "highlights":len(e.get("highlights", [])),
                "color":     _book_color(fp, self.config),
            })
        return books

    def _books_for_tab(self, tab: str) -> list[dict]:
        books = self._all_books()
        if self.active_tag:
            books = [b for b in books if self.active_tag in b["tags"]]
        if tab == "READING":
            books = [b for b in books if b["status"] == "reading"]
            books.sort(key=lambda b: b["line"] / max(b["total"], 1), reverse=True)
        elif tab == "UNREAD":
            books = [b for b in books if b["status"] == "unread"]
            books.sort(key=lambda b: b["total"], reverse=True)
        elif tab == "FAVORITES":
            books = [b for b in books if b["favorite"]]
            books.sort(key=lambda b: b["line"] / max(b["total"], 1), reverse=True)
        elif tab == "ABANDONED":
            books = [b for b in books if b["status"] == "abandoned"]
            books.sort(key=lambda b: b["line"] / max(b["total"], 1), reverse=True)
        elif tab == "READ":
            books = [b for b in books if b["status"] == "read"]
            books.sort(key=lambda b: b.get("title","").lower())
        return books

    def _all_tags(self) -> list[str]:
        tags = set()
        for b in self._all_books():
            tags.update(b["tags"])
        return sorted(tags)

    # --------------------------------------------------------------- paint

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Solid background — no PDF showing through
        painter.fillRect(0, 0, w, h, UI_BG)

        # Tabs
        self._tab_rects = []
        self._paint_tabs(painter, w)

        # Content area
        content_y = TAB_H * 2 + 4
        content_h = h - content_y - TAG_BAR_H - LIB_STATUS_H - (26 if self._cmd_mode else 0)

        if self.tab == "SETTINGS":
            self._paint_settings(painter, 0, content_y, w, content_h)
        elif self.tab in ("EDIT META", "SEARCH"):
            painter.setPen(AMBER_DARK)
            painter.setFont(_ui_font(13))
            painter.drawText(QRect(0, content_y, w, content_h),
                             Qt.AlignmentFlag.AlignCenter,
                             f"{self.tab}\n\n(coming soon)")
        elif self.tab == "READ":
            self._paint_read_tab(painter, 0, content_y, w, content_h)
        else:
            self._paint_blocks(painter, 0, content_y, w, content_h)

        # Tag bar
        tag_y = h - TAG_BAR_H - LIB_STATUS_H - (26 if self._cmd_mode else 0)
        self._paint_tag_bar(painter, 0, tag_y, w)

        # Status bar
        stat_y = h - LIB_STATUS_H - (26 if self._cmd_mode else 0)
        self._paint_lib_status(painter, 0, stat_y, w)

    def _paint_tabs(self, painter: QPainter, w: int):
        tw   = w // 4
        font_b = _ui_font(9, bold=True)
        painter.setFont(font_b)

        all_tabs = LIBRARY_TABS_ROW1 + LIBRARY_TABS_ROW2
        for i, tab in enumerate(all_tabs):
            row = 0 if tab in LIBRARY_TABS_ROW1 else 1
            col = (LIBRARY_TABS_ROW1.index(tab) if row == 0
                   else LIBRARY_TABS_ROW2.index(tab))
            x    = col * tw
            y    = row * TAB_H
            rect = QRect(x, y, tw - 2, TAB_H - 2)
            self._tab_rects.append((rect, tab))

            active = tab == self.tab
            painter.fillRect(rect, AMBER_INV_BG if active else UI_BG)
            painter.setPen(AMBER_BRIGHT if active else AMBER_DARK)
            painter.drawRect(rect)
            painter.setPen(AMBER_INV_FG if active else AMBER_DIM)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tab)

    def _current_books(self) -> list[dict]:
        """Return books for current tab/tag/overflow level."""
        if self._overflow_stack:
            return self._overflow_stack[-1]
        return self._books_for_tab(self.tab)

    def _paint_blocks(self, painter: QPainter, x: int, y: int, w: int, h: int):
        self._book_rects    = []
        self._overflow_rect = None
        books = self._current_books()

        if not books:
            painter.setPen(AMBER_DARK)
            painter.setFont(_ui_font(13))
            painter.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter,
                             f"No books in {self.tab.lower()}")
            return

        sat      = STATUS_SAT.get(self.tab.lower(), 1.0)
        padding  = 4
        MIN_W    = 100
        MIN_H    = 60
        MIN_AREA = MIN_W * MIN_H
        VP_W     = w - padding * 2
        VP_H     = h - padding * 2
        VP_AREA  = VP_W * VP_H

        def _unread(b):
            tp   = max(b["total_pages"], 1)
            read = round(b["line"] / max(b["total"], 1) * tp)
            flip = bool(self.config.get("library_flip_mode"))
            if self.tab == "READ":
                return tp  # read books always use total
            if flip:
                return max(1, read)   # flip: size by pages READ
            else:
                return max(1, tp - read)  # normal: size by pages REMAINING

        unread      = [_unread(b) for b in books]
        # Use fourth-root scaling for more pronounced size variation
        scaled      = [max(1, u ** 0.25) for u in unread]
        total_s     = max(sum(scaled), 1)

        # Normalize: each book's area proportional to scaled unread pages
        norm_areas  = [(s / total_s) * VP_AREA for s in scaled]

        # Split into visible (area >= MIN_AREA) and overflow
        visible     = [(b, a) for b, a in zip(books, norm_areas) if a >= MIN_AREA]
        ov_books    = [b for b, a in zip(books, norm_areas) if a < MIN_AREA]
        ov_area     = sum(a for b, a in zip(books, norm_areas) if a < MIN_AREA)

        if not visible and ov_books:
            # Everything too small — show them all at min anyway
            visible  = [(b, MIN_AREA) for b in books]
            ov_books = []
            ov_area  = 0

        # Build layout list: visible books + overflow cell (if needed)
        if ov_books:
            OVFL = {"_overflow": True}
            layout_books = [b for b, _ in visible] + [OVFL]
            layout_areas = [a for _, a in visible] + [max(MIN_AREA, ov_area)]
        else:
            layout_books = [b for b, _ in visible]
            layout_areas = [a for _, a in visible]

        # Single squarify pass — overflow cell participates naturally
        all_rects = _squarify(layout_books, layout_areas,
                               x + padding, y + padding,
                               VP_W, VP_H, padding, MIN_W, MIN_H)

        # all_rects IS visible_pairs — rename for clarity
        visible_pairs = all_rects

        mono   = _ui_font(9)
        mono_b = _ui_font(9, bold=True)

        for idx, (b, rect) in enumerate(visible_pairs):
            is_overflow = b.get("_overflow", False) if isinstance(b, dict) else False
            selected    = (idx == self._cursor_idx) and not self._cmd_mode

            if is_overflow:
                # Draw overflow cell
                painter.fillRect(rect, AMBER_INV_BG if selected else UI_BG)
                xfg = AMBER_INV_FG if selected else AMBER_DIM
                painter.setFont(_ui_font(9))
                painter.setPen(xfg)
                fm = QFontMetrics(_ui_font(9))
                cw = max(fm.horizontalAdvance("X"), 1)
                ch = 14
                for row in range(rect.top()+4, rect.bottom()-18, ch):
                    for col in range(rect.left()+4, rect.right()-4, cw):
                        painter.drawText(col, row+ch-2, "X")
                label = f"+{len(ov_books)} more"
                cats = {"unread": 0, "reading": 0, "read": 0, "abandoned": 0}
                for b in ov_books:
                    s = b.get("status", "unread")
                    if s in cats: cats[s] += 1
                cat_str = ""
                if cats["unread"]:    cat_str += f"{cats['unread']}U"
                if cats["reading"]:   cat_str += f"{cats['reading']}R"
                if cats["read"]:      cat_str += f"{cats['read']}D"
                if cats["abandoned"]: cat_str += f"{cats['abandoned']}A"
                label = f"+{len(ov_books)}/{cat_str}" if cat_str else f"+{len(ov_books)}"
                painter.fillRect(QRect(rect.left(), rect.bottom()-18, rect.width(), 18), UI_BG)
                painter.setPen(AMBER_BRIGHT if selected else AMBER)
                painter.setFont(_ui_font(9, bold=True))
                painter.drawText(QRect(rect.left(), rect.bottom()-18, rect.width(), 18),
                                 Qt.AlignmentFlag.AlignCenter, label)
                painter.setPen(AMBER_BRIGHT if selected else AMBER_DARK)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
                self._book_rects.append((rect, None))
                self._overflow_rect = rect
            else:
                # Draw book cell
                color = _adjust_sat(b["color"], sat)
                if selected:
                    painter.fillRect(rect, AMBER_INV_BG)
                    text_primary = AMBER_INV_FG
                    text_dim     = QColor(60, 40, 0)
                    text_darker  = QColor(80, 60, 0)
                else:
                    painter.fillRect(rect, color)
                    painter.fillRect(rect, QColor(0, 0, 0, 120))
                    text_primary = AMBER_BRIGHT
                    text_dim     = AMBER_DIM
                    text_darker  = AMBER_DARK

                accent = AMBER_INV_FG if selected else _adjust_sat(b["color"], min(sat*1.3, 1.0))
                painter.fillRect(QRect(rect.x(), rect.y(), 4, rect.height()), accent)
                painter.setPen(AMBER_BRIGHT if selected else QColor(255, 255, 255, 20))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)

                if b["favorite"]:
                    painter.setPen(AMBER_BRIGHT if not selected else AMBER_INV_FG)
                    painter.setFont(_ui_font(10))
                    painter.drawText(QRect(rect.x()+rect.width()-20, rect.y()+4, 16, 16),
                                     Qt.AlignmentFlag.AlignCenter, "★")

                inner  = QRect(rect.x()+10, rect.y()+8, rect.width()-20, rect.height()-16)
                line_h = 16
                metar  = f"N{b['notes']}B{b['bookmarks']}H{b['highlights']}R{int(b['line']/max(b['total'],1)*100)}P{b['total_pages']}"
                txt_col = AMBER_INV_FG if selected else AMBER_BRIGHT
                texts  = [
                    (metar,       mono,   txt_col),
                    (b["title"],  mono_b, txt_col),
                    (b["author"], mono,   txt_col),
                ]
                ty = inner.top()
                for txt, font, col in texts:
                    if ty + line_h > inner.bottom(): break
                    painter.setFont(font); painter.setPen(col)
                    painter.drawText(rect.x()+10, ty+line_h-2,
                        QFontMetrics(font).elidedText(
                            txt, Qt.TextElideMode.ElideRight, inner.width()))
                    ty += line_h

                self._book_rects.append((rect, b["filepath"]))

        # Clamp cursor
        if self._cursor_idx >= len(self._book_rects):
            self._cursor_idx = max(0, len(self._book_rects) - 1)

        mono   = _ui_font(9)
        mono_b = _ui_font(9, bold=True)

        for idx, (b, rect) in enumerate(visible_pairs):
            selected = (idx == self._cursor_idx) and not self._cmd_mode

            if selected:
                interior = AMBER_INV_BG
                border   = AMBER_BRIGHT
                txt_col  = AMBER_INV_FG
            else:
                interior = AMBER_DARK
                border   = AMBER
                txt_col  = AMBER_BRIGHT

            painter.fillRect(rect, interior)
            painter.setPen(border)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            if b["favorite"]:
                painter.setPen(txt_col)
                painter.setFont(_ui_font(10))
                painter.drawText(QRect(rect.x()+rect.width()-20, rect.y()+4, 16, 16),
                                 Qt.AlignmentFlag.AlignCenter, "★")

            inner  = QRect(rect.x()+8, rect.y()+6, rect.width()-16, rect.height()-12)
            line_h = 16
            metar  = f"N{b['notes']}B{b['bookmarks']}H{b['highlights']}R{int(b['line']/max(b['total'],1)*100)}P{b['total_pages']}"
            texts  = [
                (metar,       _ui_font(8),            txt_col),
                (b["title"],  _ui_font(9, bold=True), txt_col),
                (b["author"], _ui_font(8),            txt_col),
            ]
            ty = inner.top()
            for txt, font, col in texts:
                if ty + line_h > inner.bottom(): break
                painter.setFont(font); painter.setPen(col)
                painter.drawText(rect.x()+8, ty+line_h-2,
                    QFontMetrics(font).elidedText(
                        txt, Qt.TextElideMode.ElideRight, inner.width()))
                ty += line_h

            self._book_rects.append((rect, b["filepath"]))

        # Clamp cursor
        if self._cursor_idx >= len(self._book_rects):
            self._cursor_idx = max(0, len(self._book_rects) - 1)

    def _paint_read_tab(self, painter: QPainter, x: int, y: int, w: int, h: int):
        self._book_rects = []
        books = self._books_for_tab("READ")
        use_size = self.config.get("read_tab_sizing") == "lines"

        if not books:
            painter.setPen(AMBER_DARK)
            painter.setFont(_ui_font(13))
            painter.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter,
                             "No finished books yet")
            return

        mono   = _ui_font(9)
        mono_b = _ui_font(9, bold=True)
        bar_h  = 28
        pad    = 3
        cur_y  = y + pad

        if use_size:
            # Variable height by total line count
            total = sum(b["total"] for b in books)
            for b in books:
                bh    = max(bar_h, int(h * b["total"] / max(total, 1)))
                rect  = QRect(x + pad, cur_y, w - pad*2, bh)
                self._paint_read_bar(painter, rect, b, mono, mono_b, full=True)
                self._book_rects.append((rect, b["filepath"]))
                cur_y += bh + pad
        else:
            # Flat bars
            for b in books:
                rect = QRect(x + pad, cur_y, w - pad*2, bar_h)
                self._paint_read_bar(painter, rect, b, mono, mono_b, full=False)
                self._book_rects.append((rect, b["filepath"]))
                cur_y += bar_h + pad

    def _paint_read_bar(self, painter, rect, b, mono, mono_b, full=False):
        color = _adjust_sat(b["color"], 0.6)
        dark  = QColor(0, 0, 0, 140)
        painter.fillRect(rect, QColor(16,16,16))
        painter.fillRect(QRect(rect.x(), rect.y(), 4, rect.height()), color)

        if full:
            painter.fillRect(rect, dark)

        metar = f"N{b['notes']}B{b['bookmarks']}H{b['highlights']}P{b['total_pages']}"
        stars = "★" * (b["rating"] or 0) + "☆" * (5 - (b["rating"] or 0))

        painter.setPen(AMBER_BRIGHT)
        painter.setFont(mono_b)
        painter.drawText(rect.x() + 12, rect.y() + 18, b["title"][:40])

        painter.setPen(AMBER_DIM)
        painter.setFont(mono)
        mid_x = rect.x() + rect.width() // 3
        painter.drawText(mid_x, rect.y() + 18, b["author"][:30])

        right_x = rect.x() + rect.width() - 220
        painter.drawText(right_x, rect.y() + 18, metar)

        painter.setPen(AMBER_BRIGHT)
        painter.drawText(rect.x() + rect.width() - 90, rect.y() + 18, stars)

        painter.setPen(QColor(255, 255, 255, 20))
        painter.drawRect(rect)

    def _paint_settings(self, painter: QPainter, x: int, y: int, w: int, h: int):
        mono   = _ui_font(10)
        mono_b = _ui_font(10, bold=True)
        head   = _ui_font(10, bold=True)

        settings_ref = [
            ("LIBRARY", [
                ("library_dir",       self.config.get("library_dir") or _default_lib_dir(),
                 "set library_dir <path>",       "Root folder scanned for PDFs"),
                ("library_recursive", str(self.config.get("library_recursive") or False),
                 "set library_recursive true",   "Scan subdirectories too"),
                ("read_tab_sizing",   self.config.get("read_tab_sizing") or "flat",
                 "set read_tab_sizing lines",    "Read tab: 'flat' (default) or 'lines'"),
            ]),
            ("APPEARANCE", [
                ("current_swatch",       self.config.get("current_swatch") or "amber",
                 "ctrl+k/l to cycle",             "Active colour swatch"),
                ("indicator_color",      self.config.get("indicator_color"),
                 "ctrl+o/p to cycle",             "Main line indicator colour"),
                ("background_color",     self.config.get("background_color"),
                 "set background_color #1a1a1a",  "Main background"),
                ("statusbar_color",       self.config.get("statusbar_color"),
                 "set statusbar_color #111111",   "Status bar background"),
                ("statusbar_text_color",  self.config.get("statusbar_text_color"),
                 "set statusbar_text_color #888", "Status bar text"),
                ("indicator_color",       self.config.get("indicator_color"),
                 "set indicator_color #ff4444",   "Current-line indicator"),
                ("highlight_alpha",       str(self.config.get("highlight_alpha")),
                 "set highlight_alpha 35",         "Highlight opacity (0–255)"),
                ("highlight_height",      str(self.config.get("highlight_height")),
                 "set highlight_height 20",        "Highlight band height px"),
            ]),
            ("BOOK SWATCH", [
                ("library_swatch",   str(self.config.get("library_swatch") or DEFAULT_SWATCH),
                 "set library_swatch [\"#hex\",...] ", "JSON list of color hex codes"),
            ]),
            ("EXPORT", [
                ("export_dir",   self.config.get("export_dir") or "(same as PDF)",
                 "set export_dir ~/exports",     "Export destination"),
                ("export_mode",  self.config.get("export_mode") or "timestamped",
                 "set export_mode running",      "timestamped or running"),
            ]),
            ("READING", [
                ("reopen_last",  str(self.config.get("reopen_last")),
                 "set reopen_last true",   "Auto-reopen last book"),
                ("midpoint",     str(self.config.get("midpoint")),
                 "set midpoint 0.42",      "Indicator lock position (0–1)"),
                ("zoom_mode",    str(self.config.get("zoom_mode")),
                 "set zoom_mode fit-width","Default zoom mode"),
                ("page_gap",     str(self.config.get("page_gap")),
                 "set page_gap 30",        "Gap between pages (px)"),
            ]),
        ]

        cur_y   = y + 12
        lh_head = 28
        lh_row  = 22
        col1    = x + 16        # key name
        col2    = x + 240       # current value  
        col3    = x + 460       # set command + description combined

        painter.setClipRect(QRect(x, y, w, h))
        for section, rows in settings_ref:
            if cur_y > y + h: break

            # Section header
            painter.fillRect(QRect(x, cur_y, w, lh_head), QColor(10,8,0))
            painter.setPen(AMBER_BRIGHT)
            painter.setFont(head)
            painter.drawText(col1, cur_y + lh_head - 6, section)
            cur_y += lh_head + 2

            painter.setFont(mono)
            for key, val, cmd, desc in rows:
                if cur_y + lh_row > y + h: break
                # Key
                painter.setPen(AMBER)
                painter.setFont(mono_b)
                painter.drawText(col1, cur_y + lh_row - 5, key)
                # Value (elided to fit column)
                painter.setPen(AMBER_DIM)
                painter.setFont(mono)
                elided_val = QFontMetrics(mono).elidedText(
                    str(val), Qt.TextElideMode.ElideRight, col3 - col2 - 16)
                painter.drawText(col2, cur_y + lh_row - 5, elided_val)
                # Command + description combined
                painter.setPen(AMBER_DARK)
                full_cmd = f"{cmd}   — {desc}"
                elided_cmd = QFontMetrics(mono).elidedText(
                    full_cmd, Qt.TextElideMode.ElideRight, w - col3 - 20)
                painter.drawText(col3, cur_y + lh_row - 5, elided_cmd)
                cur_y += lh_row

            cur_y += 8

        painter.setClipping(False)

    def _paint_tag_bar(self, painter: QPainter, x: int, y: int, w: int):
        """Bottom info bar: shows METAR + full filename of cursor book."""
        painter.fillRect(QRect(x, y, w, TAG_BAR_H), UI_BG)
        painter.setPen(AMBER_DARK)
        painter.drawLine(x, y, x + w, y)

        # Find cursor book
        txt = ""
        if self._book_rects and self._cursor_idx < len(self._book_rects):
            _, fp = self._book_rects[self._cursor_idx]
            if fp is not None:
                e  = self.history._entry(fp)
                tp = e.get("total_pages") or 1
                ln = e.get("line") or 0
                tl = max((e.get("total_lines") or 1), 1)
                n  = len(e.get("notes", []))
                b  = len(e.get("bookmarks", []))
                h  = len(e.get("highlights", []))
                r  = int(ln / tl * 100)
                metar = f"N{n}B{b}H{h}R{r}P{tp}"
                fname = os.path.basename(fp)
                txt   = f"{metar}   {fname}"
            elif fp is None:
                txt = f"+{len([b for b in self._current_books()])} overflow books"

        painter.setPen(AMBER_DIM)
        painter.setFont(_ui_font(9))
        painter.drawText(x + 10, y + TAG_BAR_H - 8, txt or "—")

    def _draw_tag_pill(self, painter, x, y, text, active, color_hex):
        mono_b = _ui_font(9, bold=True)
        fm     = QFontMetrics(mono_b)
        pw     = fm.horizontalAdvance(text) + 20
        ph     = TAG_BAR_H - 8
        bg     = QColor(color_hex) if active else UI_BG
        painter.fillRect(QRect(x, y, pw, ph), bg)
        painter.setPen(AMBER if active else AMBER_DARK)
        painter.drawRect(QRect(x, y, pw, ph))
        painter.setPen(AMBER_BRIGHT if active else AMBER_DARK)
        painter.setFont(mono_b)
        painter.drawText(QRect(x, y, pw, ph), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_lib_status(self, painter: QPainter, x: int, y: int, w: int):
        painter.fillRect(QRect(x, y, w, LIB_STATUS_H), UI_BG)
        painter.setPen(AMBER_VERY_DIM)
        painter.drawLine(x, y, x + w, y)
        painter.setPen(AMBER_DARK)
        painter.setFont(_ui_font(9))
        books = self._current_books()
        depth = f"  [+{len(self._overflow_stack)} levels deep]" if self._overflow_stack else ""
        msg   = self.status_msg or (
            f"{len(books)} book(s){depth}"
            f"  —  ↑↓←→ navigate  Space: open  Tab/Esc: back  Enter: command"
        )
        painter.drawText(x + 12, y + LIB_STATUS_H - 6, msg)

    # --------------------------------------------------------------- input

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
        return super().eventFilter(obj, event)

    def _select_current(self):
        if not self._book_rects: return
        idx = self._cursor_idx
        if idx >= len(self._book_rects): return
        _, fp = self._book_rects[idx]

        if fp is None:
            # Overflow cell — drill down using normalized areas
            books = self._current_books()
            vp_area = self.width() * self.height()
            def _u(b):
                tp = max(b["total_pages"], 1)
                return max(1, tp - round(b["line"] / max(b["total"], 1) * tp))
            unread  = [_u(b) for b in books]
            total_u = max(sum(unread), 1)
            norm    = [(u / total_u) * vp_area for u in unread]
            MIN_AREA = 100 * 60
            overflow = [b for b, a in zip(books, norm) if a < MIN_AREA]
            if overflow:
                self._overflow_stack.append(overflow)
                self._cursor_idx = 0
                self.update()
        else:
            self.hide()
            self.open_book.emit(fp)

    def _go_back(self):
        """Go back one overflow level, or close if at top."""
        if self._overflow_stack:
            self._overflow_stack.pop()
            self._cursor_idx = 0
            self.update()
        else:
            self.hide()

    def _move_cursor(self, dx: int, dy: int):
        """Move cursor by direction in the current grid."""
        n = len(self._book_rects)
        if n == 0: return

        cur = self._cursor_idx
        if cur >= n: cur = 0

        # Build a simple grid position from rect centers
        rects = [r for r, _ in self._book_rects]
        cx, cy = rects[cur].center().x(), rects[cur].center().y()

        best_idx  = cur
        best_dist = float('inf')

        for i, r in enumerate(rects):
            if i == cur: continue
            rx, ry = r.center().x(), r.center().y()
            ddx, ddy = rx - cx, ry - cy

            # Must be in roughly the right direction
            if dx != 0 and (dx > 0) != (ddx > 0): continue
            if dy != 0 and (dy > 0) != (ddy > 0): continue
            if dx == 0 and abs(ddx) > abs(ddy) * 2: continue
            if dy == 0 and abs(ddy) > abs(ddx) * 2: continue

            dist = ddx*ddx + ddy*ddy
            if dist < best_dist:
                best_dist = dist
                best_idx  = i

        self._cursor_idx = best_idx
        self.update()

    def _cycle_tab(self, direction: int):
        """Cycle through tabs without needing to press enter."""
        all_tabs = LIBRARY_TABS_ROW1 + LIBRARY_TABS_ROW2
        if self.tab not in all_tabs:
            self.tab = all_tabs[0]
        else:
            idx = all_tabs.index(self.tab)
            self.tab = all_tabs[(idx + direction) % len(all_tabs)]
        self._overflow_stack = []
        self._cursor_idx     = 0
        self.status_msg      = ""
        self._refresh_books()
        self.update()
    def keyPressEvent(self, ev: QKeyEvent):
        k    = ev.key()
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)

        if self._cmd_mode:
            if k == Qt.Key.Key_Escape:
                self._exit_command_mode()
            return

        if k == Qt.Key.Key_Escape:
            self._go_back(); return

        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if time.time() > self._cmd_cooldown:
                if ctrl:
                    self._enter_command_mode()
                else:
                    self._select_current()
            return

        if k in (Qt.Key.Key_Tab, Qt.Key.Key_Backspace):
            self._go_back(); return

        # Global mode keys
        if not self._cmd_mode:
            mw = self.parent()
            if k == Qt.Key.Key_R or k == Qt.Key.Key_L:
                self.hide(); mw.reader.setFocus(); return
            if k == Qt.Key.Key_N:
                self.hide(); mw.reader.setFocus()
                mw.reader._open_annot_panel("notes"); return
            if k == Qt.Key.Key_B:
                self.hide(); mw.reader.setFocus()
                mw.reader._open_annot_panel("bookmarks"); return
            if k == Qt.Key.Key_H:
                self.hide(); mw.reader.setFocus()
                mw.reader._open_annot_panel("highlights"); return
        if ctrl:
            if k == Qt.Key.Key_K:
                self.parent().reader._cycle_swatch(-1)
                self.update(); return
            if k == Qt.Key.Key_L:
                self.parent().reader._cycle_swatch(1)
                self.update(); return
            if k == Qt.Key.Key_Comma:
                self.parent().reader._cycle_font(-1)
                self.update(); return
            if k == Qt.Key.Key_Period:
                self.parent().reader._cycle_font(1)
                self.update(); return
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

    def wheelEvent(self, ev: QWheelEvent):
        delta = ev.angleDelta().y()
        n = len(self._book_rects)
        if n == 0 or not delta: return
        if delta < 0:
            self._cursor_idx = min(n-1, self._cursor_idx+1)
        else:
            self._cursor_idx = max(0, self._cursor_idx-1)
        self.update()

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.pos()

        # Tab clicks — switch tab immediately
        for rect, tab in self._tab_rects:
            if rect.contains(pos):
                self.tab = tab
                self._overflow_stack = []
                self._cursor_idx = 0
                self.status_msg = ""
                self._refresh_books()
                self.update()
                return

        # Tag clicks
        for rect, tag in self._tag_rects:
            if rect.contains(pos):
                self.active_tag = tag
                self._overflow_stack = []
                self._cursor_idx = 0
                self.update()
                return

        # Book clicks — first click selects, second click (or double-click) opens
        for i, (rect, fp) in enumerate(self._book_rects):
            if rect.contains(pos):
                if self._cursor_idx == i:
                    # Already selected — open it
                    self._select_current()
                else:
                    # First click — just move cursor
                    self._cursor_idx = i
                    self.update()
                return

    # ------------------------------------------------- command mode

    def _enter_command_mode(self):
        self._cmd_mode = True
        self.cmd.setVisible(True)
        self.cmd.setText(":")
        self.cmd.setFocus()
        self.cmd.setCursorPosition(len(self.cmd.text()))

    def _exit_command_mode(self):
        self._cmd_mode = False
        self._cmd_cooldown = time.time() + 0.15
        self.cmd.setVisible(False)
        self.cmd.clear()
        self.setFocus()
        self.update()

    def _execute_command(self):
        raw = self.cmd.text().lstrip(":").strip()
        self._exit_command_mode()
        if not raw:
            return
        result = self._run_lib_cmd(raw)
        if result:
            self.status_msg = result
            self.update()

    def _run_lib_cmd(self, text: str) -> Optional[str]:
        parts = text.split(None, 2)
        if not parts:
            return None
        cmd = parts[0].lower()

        if cmd == "set":
            if len(parts) < 3:
                return "usage: set <key> <value>"
            result = self.config.set(parts[1], parts[2])
            self._refresh_books()
            self.update()
            return result

        if cmd == "rescan":
            self._refresh_books()
            self.update()
            return f"scanned {self.config.get('library_dir') or _default_lib_dir()}"

        if cmd in ("help", "?", "man"):
            return "commands: set <key> <val>  rescan  fav  unfav  q"

        if cmd == "fav":
            fp = self._current_fp()
            if not fp: return "open a book first"
            return self.history.set_favorite(fp, True)

        if cmd == "unfav":
            fp = self._current_fp()
            if not fp: return "open a book first"
            return self.history.set_favorite(fp, False)

        if cmd in ("q", "quit", "exit"):
            self.hide()
            return None

        return f"unknown: {cmd}"

    def _current_fp(self) -> Optional[str]:
        """Get filepath of currently open book from parent reader."""
        try:
            return self.parent().reader.document.filepath
        except Exception:
            return None
class MainWindow(QMainWindow):
    def __init__(self, config: Config, history: History, initial_file=None):
        super().__init__()
        self.setWindowTitle("ScrollReader")
        self.resize(1100, 820)
        self.setStyleSheet(f"background: {config.get('background_color')};")
        self.reader = ReaderWidget(config, history)
        self.setCentralWidget(self.reader)
        self.reader.setFocus()

        # Library overlay
        self.library = LibraryWidget(config, history, parent=self)
        self.library.hide()
        self.library.open_book.connect(self.reader.load_document)

        load_path = initial_file
        if not load_path and config.get("reopen_last"):
            last = history.last_file()
            if last and os.path.exists(last): load_path = last

        if load_path:
            path = load_path
            QTimer.singleShot(80, lambda: self.reader.load_document(path))

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

    def keyPressEvent(self, ev: QKeyEvent):
        k    = ev.key()
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)

        if k == Qt.Key.Key_F11:
            if self.isFullScreen(): self.showMaximized()
            else:                   self.showFullScreen()
            return

        # Global mode keys — work from any screen, not when command bar open
        if not ctrl and not self.reader.command_mode:
            if k == Qt.Key.Key_R:
                # Return to reading mode — close library and any panel
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

        if ctrl:
            if k == Qt.Key.Key_K:
                self.reader._cycle_swatch(-1)
                self.update()
                if self.library.isVisible(): self.library.update()
                return
            if k == Qt.Key.Key_L:
                self.reader._cycle_swatch(1)
                self.update()
                if self.library.isVisible(): self.library.update()
                return
            if k == Qt.Key.Key_Comma:
                self.reader._cycle_font(-1)
                self.update(); return
            if k == Qt.Key.Key_Period:
                self.reader._cycle_font(1)
                self.update(); return

        super().keyPressEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.library.isVisible():
            self.library.setGeometry(self.centralWidget().geometry())


def main():
    app     = QApplication(sys.argv)
    app.setApplicationName("ScrollReader")
    _load_vga_font()
    config  = Config()
    swatch = config.get("current_swatch") or "amber"
    _apply_swatch(swatch, config)
    _apply_theme(config)
    _UI_FONT_OFFSET_ref[0] = int(config.get("ui_font_offset") or 0)
    # Load saved font
    fonts = _scan_fonts()
    fidx  = int(config.get("current_font_idx") or 0)
    if fonts and fidx < len(fonts):
        _UI_FONT_FAMILY_ref[0] = _load_font_by_path(fonts[fidx]) or _UI_FONT_FAMILY_ref[0]
    history = History()
    initial = sys.argv[1] if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else None
    window  = MainWindow(config, history, initial_file=initial)
    window.showMaximized()
    window.reader.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
