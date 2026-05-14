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
                          QFontMetrics)
try:
    from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
    HAS_PRINT = True
except ImportError:
    HAS_PRINT = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_BAR_H = 28
APP_DIR      = Path.home() / ".scrollreader"
CONFIG_PATH  = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.json"

DEFAULT_CONFIG = {
    "reopen_last":           True,
    "midpoint":              0.42,
    "zoom_mode":             "fit-width",
    "zoom_fixed":            1.5,
    "indicator_color":       "#ff4444",
    "highlight_alpha":       35,
    "highlight_height":      20,
    "highlight_offset":      0,
    "saved_highlight_color": "#4488ff",
    "saved_highlight_alpha": 45,
    "bookmark_color":        "#ffaa00",
    "note_color":            "#44ff88",
    "background_color":      "#1a1a1a",
    "statusbar_color":       "#111111",
    "statusbar_text_color":  "#888888",
    "page_gap":              30,
    "export_dir":            "",
    "export_mode":           "timestamped",
    "library_dir":           "",
    "library_recursive":     False,
    "library_swatch":        [],            # empty = use built-in swatch
    "read_tab_sizing":       "flat",
    "eager_pages":           8,             # pages rendered synchronously around current position
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
        self.page_pixmaps: list[Optional[QPixmap]] = []  # None = not yet rendered
        self.page_offsets: list[int]               = []
        self.page_sizes:   list[tuple]             = []  # (w_px, h_px) at zoom
        self.lines:        list[LineInfo]          = []
        self.total_height  = 0
        self.max_width     = 0
        first = self.doc[0].rect if self.doc else fitz.Rect(0,0,612,792)
        self.natural_width  = float(first.width)
        self.natural_height = float(first.height)
        self._parse()

    def _parse(self):
        """Parse page geometry and extract text without rendering any pixels."""
        cy = 0
        for pn, page in enumerate(self.doc):
            r   = page.rect
            w   = int(r.width  * self.zoom)
            h   = int(r.height * self.zoom)
            self.page_sizes.append((w, h))
            self.page_offsets.append(cy)
            self.page_pixmaps.append(None)   # placeholder
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
        """Render a single page and cache it. Thread-safe for reading doc."""
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = self.doc[pn].get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height,
                     pix.stride, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)

    def render_range(self, start: int, end: int):
        """Render pages [start, end] synchronously."""
        start = max(0, start)
        end   = min(len(self.doc) - 1, end)
        for pn in range(start, end + 1):
            if self.page_pixmaps[pn] is None:
                self.page_pixmaps[pn] = self.render_page(pn)

    def placeholder(self, pn: int) -> QPixmap:
        """Grey placeholder pixmap for an unrendered page."""
        w, h = self.page_sizes[pn]
        pm   = QPixmap(w, h)
        pm.fill(QColor(38, 38, 38))
        return pm

    def get_pixmap(self, pn: int) -> QPixmap:
        """Return rendered pixmap or placeholder."""
        pm = self.page_pixmaps[pn]
        return pm if pm is not None else self.placeholder(pn)

    @property
    def page_count(self):
        return len(self.doc)


class RenderThread(QThread):
    page_ready = pyqtSignal(int, QPixmap)

    def __init__(self, document: PDFDocument, start_page: int):
        super().__init__()
        self.document   = document
        self.start_page = start_page
        self._cancel    = False

    def cancel(self):
        self._cancel = True

    def run(self):
        """Render pages outward from start_page, skipping already-rendered ones."""
        n      = self.document.page_count
        order  = _render_order(self.start_page, n)
        for pn in order:
            if self._cancel:
                return
            if self.document.page_pixmaps[pn] is None:
                try:
                    pm = self.document.render_page(pn)
                    self.document.page_pixmaps[pn] = pm
                    self.page_ready.emit(pn, pm)
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
        self.status_text     = "ScrollReader  —  Enter for command mode  —  open <path>"
        self.zoom_mode: str  = str(self.config.get("zoom_mode") or "fit-width")
        self.panel: Optional[dict]     = None  # annotation panel
        self._panel_scroll: int        = 0     # scroll offset for panels
        self._pending: Optional[dict]  = None  # y/n confirmation
        self._panel_rects: list        = []    # [(QRect, line_index)] for click detection
        self._render_thread: Optional[RenderThread] = None

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(600, 400)
        self.setMouseTracking(False)

        self._cmd_cooldown = 0.0   # timestamp after which Enter re-opens cmd bar

        self.cmd = QLineEdit(self)
        self.cmd.setVisible(False)
        self.cmd.installEventFilter(self)
        self.cmd.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e1e; color: #e0e0e0;
                border: none; border-top: 1px solid #333;
                padding: 3px 10px;
                font-family: "Courier New", monospace; font-size: 13px;
                selection-background-color: #444;
            }
        """)

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

    def resizeEvent(self, ev):
        self.cmd.setGeometry(0, self.height()-26, self.width(), 26)
        super().resizeEvent(ev)

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

            # Synchronously render pages around current position
            cur_page  = doc.lines[self.current_line].page_num if doc.lines else 0
            eager     = int(self.config.get("eager_pages") or 8)
            doc.render_range(cur_page - 2, cur_page + eager)

            self._update_status()
            self.update()

            # Background-render the rest
            self._start_render_thread(cur_page)

        except Exception as ex:
            self.status_text = f"error: {ex}"; self.update()

    def _start_render_thread(self, start_page: int):
        self._stop_render_thread()
        t = RenderThread(self.document, start_page)
        t.page_ready.connect(self._on_page_ready)
        t.finished.connect(self._on_render_done)
        self._render_thread = t
        t.start()

    def _stop_render_thread(self):
        if self._render_thread is not None:
            self._render_thread.cancel()
            self._render_thread.wait(200)
            self._render_thread = None

    def _on_page_ready(self, pn: int, pixmap: QPixmap):
        """Called from main thread when background renders a page."""
        if self.document:
            self.document.page_pixmaps[pn] = pixmap
            # Only repaint if this page is currently visible
            scroll = self._scroll_offset()
            py     = STATUS_BAR_H + self.document.page_offsets[pn] - scroll
            _, ph  = self.document.page_sizes[pn]
            if py + ph >= STATUS_BAR_H and py <= self.height():
                self.update()
            # Update status to show render progress
            rendered = sum(1 for p in self.document.page_pixmaps if p is not None)
            total    = self.document.page_count
            if rendered < total:
                pct = f"{100*rendered//total}%"
                self._update_status()
                self.status_text += f"  —  rendering {pct}"
                self.update()

    def _on_render_done(self):
        self._render_thread = None
        self._update_status()
        self.update()

    # ---------------------------------------------------------------- zoom

    def _compute_zoom(self, mode, peek_path=None):
        nw, nh = 612.0, 792.0
        if self.document:
            nw, nh = self.document.natural_width, self.document.natural_height
        elif peek_path and os.path.exists(peek_path):
            try:
                d = fitz.open(peek_path); r = d[0].rect; nw, nh = r.width, r.height; d.close()
            except Exception: pass
        uw = self.width() - 40
        uh = self.height() - STATUS_BAR_H - 40
        if mode == "fit-width": return max(0.1, uw/nw)
        if mode == "fit-page":  return max(0.1, uh/nh)
        if mode == "50%":  return 0.75
        if mode == "75%":  return 1.1
        if mode == "100%": return 1.5
        return float(self.config.get("zoom_fixed") or 1.5)

    def _rerender(self):
        if not self.document: return
        old = self.current_line
        self.status_text = f"rendering [{self.zoom_mode}]…"
        self.update(); QApplication.processEvents()
        try:
            self.load_document(self.document.filepath)
            self.current_line = min(old, max(0, len(self.document.lines)-1))
            self._update_status()
        except Exception as ex:
            self.status_text = f"render error: {ex}"
        self.update()

    # -------------------------------------------------------------- helpers

    def _usable_h(self):  return self.height() - STATUS_BAR_H
    def _midpoint_y(self): return self._usable_h() * float(self.config.get("midpoint"))
    def _lh(self):   return max(8, int(self._cfg("highlight_height") or 20))
    def _voff(self): return int(self._cfg("highlight_offset") or 0)

    def _scroll_offset(self):
        if not self.document or not self.document.lines: return 0.0
        ly = self.document.lines[self.current_line].abs_y
        mp = self._midpoint_y()
        if ly < mp: return 0.0
        return min(ly - mp, max(0, self.document.total_height - self._usable_h()))

    def _indicator_screen_y(self):
        if not self.document or not self.document.lines:
            return STATUS_BAR_H + int(self._midpoint_y())
        return STATUS_BAR_H + int(self.document.lines[self.current_line].abs_y - self._scroll_offset())

    def _page_x_offset(self):
        if not self.document: return 0
        return max(0, (self.width() - self.document.max_width) // 2)

    def _lines_per_screen(self):
        if not self.document or not self.document.lines: return 10
        avg = self.document.total_height / len(self.document.lines)
        return max(1, int(self._usable_h() / avg) - 2)

    def _update_status(self):
        if not self.document or not self.document.lines: return
        line  = self.document.lines[self.current_line]
        total = len(self.document.lines)
        pct   = f"{100*self.current_line/total:.0f}%" if total else "?"
        self.status_text = (
            f"{Path(self.document.filepath).name}"
            f"  —  line {self.current_line+1}/{total} ({pct})"
            f"  —  page {line.page_num+1}/{self.document.page_count}"
            f"  —  [{self.zoom_mode}]"
        )

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
        painter.fillRect(self.rect(), QColor(self.config.get("background_color")))

        if not self.document:
            painter.setPen(QColor("#444444"))
            painter.setFont(QFont("Courier New", 13))
            r = QRect(0, STATUS_BAR_H, self.width(), self.height()-STATUS_BAR_H)
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "Enter command mode → open <path>")
            self._paint_statusbar(painter)
            return

        scroll = self._scroll_offset()
        px     = self._page_x_offset()
        lh     = self._lh()
        voff   = self._voff()
        dlines = self.document.lines
        total  = len(dlines)

        # Pages
        for i in range(self.document.page_count):
            py = STATUS_BAR_H + self.document.page_offsets[i] - scroll
            pw, ph = self.document.page_sizes[i]
            if py + ph >= STATUS_BAR_H and py <= self.height():
                painter.drawPixmap(px, int(py), self.document.get_pixmap(i))

        # Saved highlights (blue)
        sh_col = QColor(self._cfg("saved_highlight_color") or "#4488ff")
        sh_col.setAlpha(int(self._cfg("saved_highlight_alpha") or 45))
        for h in self.history._entry(self.document.filepath).get("highlights", []):
            sl, el = h.get("start_line",0), h.get("end_line",0)
            if sl >= total or el >= total: continue
            sy = int(STATUS_BAR_H + dlines[sl].abs_y - scroll + voff)
            ey = int(STATUS_BAR_H + dlines[el].abs_y - scroll + voff + lh)
            if ey >= STATUS_BAR_H and sy <= self.height():
                painter.fillRect(QRect(px, sy, self.document.max_width, ey-sy), sh_col)

        # Bookmark markers (amber triangle)
        bm_col = QColor(self._cfg("bookmark_color") or "#ffaa00")
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(bm_col)
        for bm in self.history._entry(self.document.filepath).get("bookmarks", []):
            bl = bm.get("line",0)
            if bl >= total: continue
            by = int(STATUS_BAR_H + dlines[bl].abs_y - scroll + voff)
            if STATUS_BAR_H <= by <= self.height():
                tx = max(2, px-16)
                painter.drawPolygon(QPolygon([QPoint(tx,by), QPoint(tx,by+lh), QPoint(tx+10,by+lh//2)]))

        # Note markers (green dot)
        nc = QColor(self._cfg("note_color") or "#44ff88")
        painter.setBrush(nc)
        for n in self.history._entry(self.document.filepath).get("notes", []):
            nl = n.get("line",0)
            if nl >= total: continue
            ny = int(STATUS_BAR_H + dlines[nl].abs_y - scroll + voff + lh//2)
            if STATUS_BAR_H <= ny <= self.height():
                painter.drawEllipse(QPoint(max(2, px-28), ny), 4, 4)

        # Current-line highlight
        ind_y = self._indicator_screen_y() + voff
        painter.fillRect(QRect(0, ind_y-2, self.width(), lh),
                         QColor(255, 68, 68, int(self._cfg("highlight_alpha"))))

        # Current-line indicator triangle
        ind_col = QColor(self._cfg("indicator_color"))
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(ind_col)
        tx = max(2, px-16); ty = ind_y + lh//2; sz = 10
        painter.drawPolygon(QPolygon([QPoint(tx,ty-sz//2), QPoint(tx,ty+sz//2), QPoint(tx+sz,ty)]))

        # Status bar (on top)
        self._paint_statusbar(painter)

        # Panels and overlays
        if self.panel:
            if self.panel.get("kind") == "help":
                self._paint_help_panel(painter)
            else:
                self._paint_panel(painter)
        if self._pending: self._paint_confirm(painter)

    def _paint_statusbar(self, painter):
        painter.fillRect(QRect(0, 0, self.width(), STATUS_BAR_H),
                         QColor(self.config.get("statusbar_color") or "#111111"))
        painter.setPen(QColor("#2a2a2a"))
        painter.drawLine(0, STATUS_BAR_H-1, self.width(), STATUS_BAR_H-1)
        painter.setPen(QColor(self.config.get("statusbar_text_color") or "#888888"))
        painter.setFont(QFont("Courier New", 10))
        painter.drawText(10, STATUS_BAR_H-8, self.status_text)

    def _paint_panel(self, painter):
        self._panel_rects = []
        pw   = max(340, self.width()//2)
        px   = self.width() - pw
        py   = STATUS_BAR_H
        ph   = self.height() - STATUS_BAR_H

        painter.fillRect(QRect(px, py, pw, ph), QColor(18, 18, 18, 230))
        painter.setPen(QColor("#333333"))
        painter.drawLine(px, py, px, self.height())

        painter.setPen(QColor("#999999"))
        painter.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        painter.drawText(px+14, py+22, self.panel["title"])
        painter.setPen(QColor("#2a2a2a"))
        painter.drawLine(px, py+30, self.width(), py+30)

        items  = self.panel.get("items", [])
        kind   = self.panel.get("kind", "")
        y      = py + 50
        row_h  = 38

        painter.setFont(QFont("Courier New", 10))
        if not items:
            painter.setPen(QColor("#444444"))
            painter.drawText(px+14, y+14, "(none)")
        else:
            for item in items:
                if y + row_h > self.height() - 20:
                    painter.setPen(QColor("#444444"))
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

                painter.setPen(QColor("#888888"))
                painter.drawText(px+14, y+14, loc)
                if body:
                    metrics = painter.fontMetrics()
                    painter.setPen(QColor("#cccccc"))
                    painter.drawText(px+14, y+28, metrics.elidedText(body, Qt.TextElideMode.ElideRight, pw-30))

                painter.setPen(QColor("#222222"))
                painter.drawLine(px+8, y+row_h, self.width()-8, y+row_h)
                y += row_h

        painter.setPen(QColor("#333333"))
        painter.setFont(QFont("Courier New", 9))
        painter.drawText(px+14, self.height()-8, "click to jump  —  Esc to close")

    def _paint_confirm(self, painter):
        w, h = 460, 90
        x = (self.width()-w)//2
        y = (self.height()-h)//2
        painter.fillRect(QRect(x, y, w, h), QColor(20, 20, 20, 245))
        painter.setPen(QColor("#444444"))
        painter.drawRect(QRect(x, y, w, h))
        painter.setFont(QFont("Courier New", 11))
        painter.setPen(QColor("#dddddd"))
        painter.drawText(QRect(x, y, w, h*6//10), Qt.AlignmentFlag.AlignCenter, self._pending["msg"])
        painter.setPen(QColor("#666666"))
        painter.setFont(QFont("Courier New", 10))
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
        ("NAVIGATION COMMANDS", None, [
            ("gl<N>  /  gotoline<N>",    "Jump to line N"),
            ("gp<N>  /  gotopage<N>",    "Jump to page N"),
            ("lb[N]  /  lineback[N]",    "Go back N lines (default 1)"),
            ("lf[N]  /  lineforward[N]", "Go forward N lines (default 1)"),
            ("pb[N]  /  pageback[N]",    "Go back N pages (default 1)"),
            ("pf[N]  /  pageforward[N]", "Go forward N pages (default 1)"),
        ]),
        ("RANGE SYNTAX", None, [
            ("", "Many commands accept a range specifier appended directly to the command:"),
            ("",                    ""),
            ("<cmd>",               "Current line or page"),
            ("<cmd><N>",            "Line/page N  (absolute)"),
            ("<cmd><A>-<B>",        "Lines/pages A through B  (absolute, A must be ≤ B)"),
            ("<cmd><fwd>[;<back>]", "fwd lines/pages forward, back lines/pages back from current"),
            ("",                    ""),
            ("", "For hl / hp only, a single number always means forward:"),
            ("hl5",       "→  highlight current + next 5 lines"),
            ("hl5;3",     "→  highlight back 3, current, forward 5"),
            ("hl;3",      "→  highlight back 3 + current"),
            ("hl40-89",   "→  highlight lines 40–89  (absolute)"),
            ("",          ""),
            ("", "For all other ranged commands, a single number is absolute position:"),
            ("rl40",      "→  remove annotations at line 40"),
            ("rl5;3",     "→  remove fwd 5 back 3 from current line"),
            ("rp4-10",    "→  remove annotations on pages 4–10"),
        ]),
        ("NOTES — ;;text;;", None, [
            ("", "Any command that takes an annotation or revision note uses the ;; delimiter:"),
            ("", "Append ;;your note here;; to the end of any annotating command."),
            ("nl;;text;;",       "Note at current line"),
            ("nl33;;text;;",     "Note at line 33"),
            ("bl;;text;;",       "Bookmark current line with note"),
            ("hl5;;text;;",      "Highlight + note"),
        ]),
        ("ANNOTATION COMMANDS", None, [
            ("nl[N][;;note;;]",              "Add note at current line or line N"),
            ("bl[range][;;note;;]",          "Bookmark a line"),
            ("bp[range][;;note;;]",          "Bookmark a page"),
            ("hl[range][;;note;;]",          "Highlight lines  (same range syntax as everything else)"),
            ("hp[range][;;note;;]",          "Highlight pages"),
            ("", ""),
            ("", "Examples:"),
            ("hl",        "→  highlight current line"),
            ("hl5",       "→  highlight line 5"),
            ("hl40-89",   "→  highlight lines 40–89"),
            ("hl5;3",     "→  highlight fwd 5, back 3 from current"),
        ]),
        ("ANNOTATION PANELS", None, [
            ("sn  /  shownotes",       "Open notes panel"),
            ("sb  /  showbookmarks",   "Open bookmarks panel"),
            ("sh  /  showhighlights",  "Open highlights panel"),
            ("",                       "Click any item to jump to that line. Esc to close."),
        ]),
        ("REMOVE COMMANDS", None, [
            ("", "All remove commands show a y/n confirmation prompt before executing."),
            ("", "All accept an optional ;;reason;; which is logged to history."),
            ("", ""),
            ("rl[range][;;reason;;]",   "Remove all annotations touching a line range"),
            ("rp[range][;;reason;;]",   "Remove all annotations touching a page range"),
            ("rb[range][;;reason;;]",   "Remove bookmarks only"),
            ("rn[range][;;reason;;]",   "Remove notes only"),
            ("rh[range][;;reason;;]",   "Remove highlights only"),
            ("removeall[;;reason;;]",   "Remove ALL annotations for this book"),
            ("removeall+",              "Wipe ALL stored data for this book (no reason logged)"),
            ("", ""),
            ("", "Partial overlap rule: if a remove range touches any part of an annotation,"),
            ("", "the entire annotation is deleted."),
        ]),
        ("EXPORT COMMANDS", None, [
            ("", "Exports write Markdown files. Two modes (set via  set export_mode <mode>):"),
            ("",          "  timestamped  — new file per export: title_YYYYMMDD_HHMMSS.md"),
            ("",          "  running      — appends to title_running.md"),
            ("", "Default save location: same folder as the PDF. Change with  set export_dir <path>"),
            ("", ""),
            ("e",                     "Export all annotations"),
            ("el[range]",             "Export by line range"),
            ("ep[range]",             "Export by page range"),
            ("eb[range]",             "Export bookmarks only"),
            ("en[range]",             "Export notes only"),
            ("eh[range]",             "Export highlights only"),
            ("", ""),
            ("", "Examples:"),
            ("el40-89",               "→  export all annotations on lines 40–89"),
            ("ep2-5",                 "→  export annotations on pages 2–5"),
            ("eb45",                  "→  export bookmark at line 45"),
            ("el5;3",                 "→  export fwd 5 back 3 from current line"),
        ]),
        ("ZOOM", None, [
            ("zoom fit-width",   "Fit page width to window  (default)"),
            ("zoom fit-page",    "Fit full page height to window"),
            ("zoom 50%",         "Small fixed zoom"),
            ("zoom 75%",         "Medium fixed zoom"),
            ("zoom 100%",        "Large fixed zoom"),
            ("zoom cycle",       "Step through zoom modes in order"),
        ]),
        ("BOOK METADATA", None, [
            ("bookinfo",                     "Show title, author, status, progress, annotation counts"),
            ("setmeta title <value>",        "Set book title"),
            ("setmeta author <value>",       "Set author"),
            ("setmeta status <value>",       "Set status: unread / reading / read / abandoned"),
            ("setmeta rating <1-5>",         "Set rating"),
            ("setmeta tags <a,b,c>",         "Set comma-separated tags"),
        ]),
        ("PER-BOOK DISPLAY OVERRIDES", None, [
            ("bookset <key> <value>",  "Override a display setting for the current book only."),
            ("", ""),
            ("bookset indicator_color #ff4444",      "Current-line indicator colour"),
            ("bookset highlight_alpha 35",           "Opacity of current-line highlight band (0–255)"),
            ("bookset highlight_height 20",          "Height in pixels of the highlight band"),
            ("bookset highlight_offset 0",           "Vertical nudge of highlight band (negative = up)"),
            ("bookset saved_highlight_color #4488ff","Colour of saved highlights"),
            ("bookset saved_highlight_alpha 45",     "Opacity of saved highlights"),
            ("bookset bookmark_color #ffaa00",       "Colour of bookmark margin markers"),
            ("bookset note_color #44ff88",           "Colour of note margin dots"),
        ]),
        ("GLOBAL CONFIG", None, [
            ("set <key> <value>",    "Change a global config value (saved to ~/.scrollreader/config.json)"),
            ("showconfig",           "Print all current config values to the status bar"),
            ("", ""),
            ("set reopen_last true/false",       "Auto-reopen last book on launch"),
            ("set midpoint 0.42",                "Screen fraction where indicator locks (0.0–1.0)"),
            ("set zoom_mode fit-width",          "Default zoom mode"),
            ("set background_color #1a1a1a",     "Background colour"),
            ("set statusbar_color #111111",      "Status bar background"),
            ("set statusbar_text_color #888888", "Status bar text colour"),
            ("set page_gap 30",                  "Pixel gap between PDF pages"),
            ("set export_dir ~/exports",         "Default export directory (empty = PDF folder)"),
            ("set export_mode timestamped",      "Export mode: timestamped or running"),
        ]),
        ("FILES", None, [
            ("~/.scrollreader/config.json",   "Global configuration"),
            ("~/.scrollreader/history.json",  "Per-book history, annotations, and metadata"),
            ("", "These are plain JSON — safe to edit in any text editor."),
        ]),
        ("PRINT", None, [
            ("pd  /  printdialog",    "Open system print dialog for the whole document"),
            ("pp[range]  /  printpage[range]",  "Print pages (same range syntax as everything else)"),
            ("", ""),
            ("pp",        "→  print current page"),
            ("pp5",       "→  print page 5"),
            ("pp2-8",     "→  print pages 2–8"),
            ("pp3;1",     "→  print current page, fwd 3, back 1"),
            ("pd",        "→  open print dialog (lets you choose printer, copies, etc.)"),
            ("", ""),
            ("", "Requires PyQt6.QtPrintSupport to be installed."),
        ]),
        ("OTHER", None, [
            ("help  /  man  /  ?",  "Show this help panel"),
            ("q  /  quit  /  exit", "Quit ScrollReader"),
        ]),
    ]

    def _paint_help_panel(self, painter: QPainter):
        pw = self.width()
        py = STATUS_BAR_H
        ph = self.height() - STATUS_BAR_H

        # Background
        painter.fillRect(QRect(0, py, pw, ph), QColor(14, 14, 14, 252))

        # Clip to panel area
        painter.setClipRect(QRect(0, py, pw, ph - 24))

        margin   = 48
        col2_x   = 280
        y        = py + 20 - self._panel_scroll
        lh_body  = 19
        lh_head  = 26

        mono     = QFont("Courier New", 10)
        mono_b   = QFont("Courier New", 10, QFont.Weight.Bold)
        sans     = QFont("Courier New", 11, QFont.Weight.Bold)

        for section, _, rows in self.HELP_SECTIONS:
            # Section header
            y += 8
            painter.fillRect(QRect(0, y - 2, pw, lh_head), QColor(28, 28, 28))
            painter.setPen(QColor("#cc8844"))
            painter.setFont(sans)
            painter.drawText(margin, y + lh_head - 8, section)
            y += lh_head + 4

            for key, val in rows:
                if key == "" and val == "":
                    y += 8; continue

                if key == "":
                    # Continuation / descriptive line
                    painter.setPen(QColor("#777777"))
                    painter.setFont(mono)
                    # Word-wrap simple approach: draw in full available width
                    painter.drawText(margin, y + lh_body - 4, val)
                    y += lh_body
                else:
                    # Key column (left, amber)
                    painter.setPen(QColor("#ddaa55"))
                    painter.setFont(mono_b)
                    painter.drawText(margin, y + lh_body - 4, key)
                    if val:
                        # Value column (right, grey)
                        painter.setPen(QColor("#999999"))
                        painter.setFont(mono)
                        painter.drawText(col2_x + margin, y + lh_body - 4, val)
                    y += lh_body

            y += 6  # section gap

        painter.setClipping(False)

        # Footer bar
        painter.fillRect(QRect(0, self.height()-24, pw, 24), QColor(14, 14, 14))
        painter.setPen(QColor("#2a2a2a"))
        painter.drawLine(0, self.height()-24, pw, self.height()-24)
        painter.setPen(QColor("#444444"))
        painter.setFont(QFont("Courier New", 9))
        painter.drawText(margin, self.height()-8, "↑ ↓  PgUp  PgDn  scroll  —  Esc to close")

    # --------------------------------------------------------------- input

    def keyPressEvent(self, ev: QKeyEvent):
        k = ev.key()

        # Confirmation overlay
        if self._pending:
            if k == Qt.Key.Key_Y:
                result = self._pending["action"]()
                self._pending = None
                if result: self.status_text = result
                self.update()
            elif k in (Qt.Key.Key_N, Qt.Key.Key_Escape):
                self._pending = None
                self.status_text = "cancelled"
                self.update()
            return

        # Panel open
        if self.panel:
            if k == Qt.Key.Key_Escape:
                self.panel = None; self._panel_scroll = 0; self.update()
            elif k in (Qt.Key.Key_Down, Qt.Key.Key_Space):
                self._panel_scroll += 40; self.update()
            elif k == Qt.Key.Key_Up:
                self._panel_scroll = max(0, self._panel_scroll - 40); self.update()
            elif k == Qt.Key.Key_PageDown:
                self._panel_scroll += self.height() - STATUS_BAR_H - 60; self.update()
            elif k == Qt.Key.Key_PageUp:
                self._panel_scroll = max(0, self._panel_scroll - (self.height() - STATUS_BAR_H - 60)); self.update()
            return

        if self.command_mode:
            if k == Qt.Key.Key_Escape: self._exit_command_mode()
            return

        if   k in (Qt.Key.Key_Return, Qt.Key.Key_Enter): self._enter_command_mode()
        elif k in (Qt.Key.Key_Space, Qt.Key.Key_Down):   self._step(1)
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Backspace, Qt.Key.Key_Tab): self._step(-1)
        elif k == Qt.Key.Key_PageDown: self._step(self._lines_per_screen())
        elif k == Qt.Key.Key_PageUp:   self._step(-self._lines_per_screen())

    def wheelEvent(self, ev: QWheelEvent):
        delta = ev.angleDelta().y()
        if not delta: return
        if self.panel:
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
                    self._update_status(); self.update()
                    return
        super().mousePressEvent(ev)

    # --------------------------------------------------------- navigation

    def _step(self, delta):
        if not self.document: return
        self.current_line = max(0, min(len(self.document.lines)-1, self.current_line+delta))
        self.history.set_line(self.document.filepath, self.current_line)
        self._update_status(); self.update()

    def _jump_pages(self, direction):
        if not self.document or not self.document.lines: return
        cp = self.document.lines[self.current_line].page_num
        tp = max(0, min(self.document.page_count-1, cp+direction))
        for i, l in enumerate(self.document.lines):
            if l.page_num == tp: self.current_line = i; break
        self.history.set_line(self.document.filepath, self.current_line)
        self._update_status(); self.update()

    # ------------------------------------------------------- command mode

    def _enter_command_mode(self):
        if time.time() < self._cmd_cooldown:
            return
        self.command_mode = True
        self.cmd.setVisible(True)
        self.cmd.setText(":")
        self.cmd.setFocus()
        self.cmd.setCursorPosition(len(self.cmd.text()))

    def _exit_command_mode(self):
        self.command_mode = False
        self._cmd_cooldown = time.time() + 0.15
        self.cmd.setVisible(False)
        self.cmd.clear()
        self.setFocus(); self.update()

    def _execute_command(self):
        raw = self.cmd.text().lstrip(":").strip()
        self._exit_command_mode()   # always hide first
        if not raw: return
        result = self._run(raw)
        if result:
            self.status_text = result; self.update()

    def _run(self, text: str) -> Optional[str]:
        parsed = parse_shortcut(text)
        if parsed:
            return self._exec_shortcut(parsed)

        parts = text.split(None, 2)
        if not parts: return None
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
        if cmd in ("sn","shownotes"):        return self._open_panel("Notes",      "notes")
        if cmd in ("sb","showbookmarks"):    return self._open_panel("Bookmarks",  "bookmarks")
        if cmd in ("sh","showhighlights"):   return self._open_panel("Highlights", "highlights")
        if cmd in ("help","man","?"):        return self._open_panel("ScrollReader — Command Reference", "help")
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
        if cmd == "setmeta":
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
            self._update_status(); self.update(); return f"line {self.current_line+1}"

        if cmd == "goto_page":
            pg = max(0, min(doc.page_count-1, p["page"]-1))
            for i, l in enumerate(lines):
                if l.page_num == pg: self.current_line = i; break
            self.history.set_line(doc.filepath, self.current_line)
            self._update_status(); self.update(); return f"page {pg+1}"

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

LIBRARY_TABS   = ["READING", "UNREAD", "FAVORITES", "ABANDONED", "READ", "SETTINGS"]
TAB_H          = 38
TAG_BAR_H      = 36
LIB_STATUS_H   = 24

DEFAULT_SWATCH = [
    "#c0392b", "#8e44ad", "#2980b9", "#16a085",
    "#d35400", "#27ae60", "#2c3e50", "#7f8c8d",
    "#6c3483", "#1a5276",
]

STATUS_SAT = {
    "reading":   1.0,
    "unread":    0.7,
    "favorites": 1.0,
    "abandoned": 0.18,
    "read":      1.0,
}


def _adjust_sat(hex_color: str, factor: float) -> QColor:
    c = QColor(hex_color)
    h, s, v, a = c.getHsvF()
    c.setHsvF(h, min(1.0, s * factor), v, a)
    return c


def _scan_pdfs(directory: str, recursive: bool = False) -> list[str]:
    p = Path(directory)
    if not p.exists():
        return []
    if recursive:
        return [str(f) for f in p.rglob("*.pdf")]
    else:
        return [str(f) for f in p.glob("*.pdf")]


def _default_lib_dir() -> str:
    if sys.platform.startswith("win"):
        return str(Path(sys.executable).parent)
    return str(Path.home())


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
        self.scroll  = 0
        self.active_tag: Optional[str] = None
        self._book_rects: list         = []  # [(QRect, filepath)]
        self._tab_rects:  list         = []  # [(QRect, tab_name)]
        self._tag_rects:  list         = []  # [(QRect, tag)]
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Command bar (shared style with ReaderWidget)
        self._cmd_mode    = False
        self._cmd_cooldown = 0.0
        self.cmd = QLineEdit(self)
        self.cmd.setVisible(False)
        self.cmd.installEventFilter(self)
        self.cmd.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e1e; color: #e0e0e0;
                border: none; border-top: 1px solid #333;
                padding: 3px 10px;
                font-family: "Courier New", monospace; font-size: 13px;
                selection-background-color: #444;
            }
        """)
        self.status_msg = ""

    def resizeEvent(self, ev):
        self.cmd.setGeometry(0, self.height() - 26, self.width(), 26)
        super().resizeEvent(ev)

    def showEvent(self, ev):
        self.scroll = 0
        self._refresh_books()
        self.setFocus()
        super().showEvent(ev)

    # ---------------------------------------------------------------- data

    def _refresh_books(self):
        """Scan library dir and merge with history."""
        lib_dir   = self.config.get("library_dir") or _default_lib_dir()
        recursive = bool(self.config.get("library_recursive") or False)
        found     = _scan_pdfs(lib_dir, recursive)
        # Ensure scanned files exist in history
        for fp in found:
            self.history._entry(fp)  # creates default entry if missing

    def _all_books(self) -> list[dict]:
        """Return list of enriched book dicts from history."""
        books = []
        for fp, e in self.history.data.items():
            if not os.path.exists(fp):
                continue
            color = e.get("library_color")
            if not color:
                swatch = self.config.get("library_swatch") or DEFAULT_SWATCH
                if isinstance(swatch, str):
                    try: swatch = json.loads(swatch)
                    except: swatch = DEFAULT_SWATCH
                color = swatch[hash(fp) % len(swatch)]
                e["library_color"] = color
                self.history._save()
            books.append({
                "filepath":  fp,
                "title":     e.get("title") or Path(fp).stem,
                "author":    e.get("author") or "",
                "status":    e.get("status") or "unread",
                "rating":    e.get("rating") or 0,
                "tags":      e.get("tags") or [],
                "favorite":  bool(e.get("favorite")),
                "line":      e.get("line") or 0,
                "total":     e.get("total_lines") or 1,
                "notes":     len(e.get("notes", [])),
                "bookmarks": len(e.get("bookmarks", [])),
                "highlights":len(e.get("highlights", [])),
                "color":     color,
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

        # Dimmed background (PDF showing through)
        painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 185))

        # Main panel background
        panel_y = 0
        painter.fillRect(0, panel_y, w, h, QColor(18, 18, 18, 240))

        # Tabs
        self._tab_rects = []
        self._paint_tabs(painter, w)

        # Content area
        content_y = panel_y + TAB_H * 2 + 4
        content_h = h - content_y - TAG_BAR_H - LIB_STATUS_H - (26 if self._cmd_mode else 0)

        if self.tab == "SETTINGS":
            self._paint_settings(painter, 0, content_y, w, content_h)
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
        tabs_per_row = 3
        tw = w // tabs_per_row
        mono_b = QFont("Courier New", 10, QFont.Weight.Bold)
        painter.setFont(mono_b)

        for i, tab in enumerate(LIBRARY_TABS):
            row = i // tabs_per_row
            col = i  % tabs_per_row
            x   = col * tw
            y   = row * TAB_H
            rect = QRect(x, y, tw - 2, TAB_H - 2)
            self._tab_rects.append((rect, tab))

            active = tab == self.tab
            bg = QColor("#2a5a8a") if active else QColor("#1a2a3a")
            painter.fillRect(rect, bg)
            painter.setPen(QColor("#88ccff") if active else QColor("#446688"))
            painter.drawRect(rect)
            painter.setPen(QColor("#ffffff") if active else QColor("#88aacc"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tab)

    def _paint_blocks(self, painter: QPainter, x: int, y: int, w: int, h: int):
        self._book_rects = []
        books = self._books_for_tab(self.tab)
        if not books:
            painter.setPen(QColor("#444444"))
            painter.setFont(QFont("Courier New", 13))
            painter.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter,
                             f"No books in {self.tab.lower()}")
            return

        sat = STATUS_SAT.get(self.tab.lower(), 1.0)
        total_lines = sum(max(b["total"] - b["line"], 1) for b in books)
        usable_h    = h - 8
        min_h       = 80
        padding     = 6

        # Treemap-ish: pack into rows
        # Simple proportional layout: each book gets width proportional to remaining lines
        # Pack left-to-right, wrap into rows
        row_items: list[list] = []
        row: list = []
        row_lines = 0
        for b in books:
            remaining = max(b["total"] - b["line"], 1)
            row.append((b, remaining))
            row_lines += remaining
            frac = row_lines / max(total_lines, 1)
            if frac >= 0.25 or b is books[-1]:
                row_items.append((row[:], row_lines))
                row = []; row_lines = 0

        # Distribute rows evenly across available height
        n_rows   = max(len(row_items), 1)
        row_h    = max(min_h, (usable_h - padding * (n_rows + 1)) // n_rows)
        cur_y    = y + padding - self.scroll

        mono     = QFont("Courier New", 9)
        mono_b   = QFont("Courier New", 9, QFont.Weight.Bold)

        for row, row_lines in row_items:
            cur_x = x + padding
            for b, remaining in row:
                frac   = remaining / max(row_lines, 1)
                bw     = max(120, int((w - padding * (len(row) + 1)) * frac))
                bh     = row_h
                rect   = QRect(cur_x, cur_y, bw, bh)

                if cur_y + bh > y and cur_y < y + h:
                    color = _adjust_sat(b["color"], sat)
                    dark  = QColor(0, 0, 0, 120)

                    # Block background
                    painter.fillRect(rect, color)
                    painter.fillRect(rect, dark)

                    # Left accent bar
                    accent = _adjust_sat(b["color"], min(sat * 1.3, 1.0))
                    painter.fillRect(QRect(cur_x, cur_y, 4, bh), accent)

                    # Favorite star
                    if b["favorite"]:
                        painter.setPen(QColor("#ffdd00"))
                        painter.setFont(QFont("Courier New", 10))
                        painter.drawText(QRect(cur_x + bw - 20, cur_y + 4, 16, 16),
                                         Qt.AlignmentFlag.AlignCenter, "★")

                    # Text content
                    fm    = QFontMetrics(mono_b)
                    inner = QRect(cur_x + 10, cur_y + 8, bw - 20, bh - 16)
                    line_h = 16

                    progress = b["line"] / max(b["total"], 1)
                    pct_str  = f"{int(progress*100)}%"
                    metar    = f"N{b['notes']}B{b['bookmarks']}H{b['highlights']}"

                    lines_text = [
                        (b["title"],  mono_b, QColor("#ffffff")),
                        (b["author"], mono,   QColor("#aaaaaa")),
                        (f"PROG {pct_str}", mono, QColor("#88ccaa")),
                        (metar,       mono,   QColor("#8899aa")),
                    ]

                    ty = inner.top()
                    for txt, font, col in lines_text:
                        if ty + line_h > inner.bottom(): break
                        painter.setFont(font)
                        painter.setPen(col)
                        elided = QFontMetrics(font).elidedText(
                            txt, Qt.TextElideMode.ElideRight, inner.width())
                        painter.drawText(cur_x + 10, ty + line_h - 2, elided)
                        ty += line_h

                    # Border
                    painter.setPen(QColor(255, 255, 255, 25))
                    painter.drawRect(rect)

                    self._book_rects.append((rect, b["filepath"]))

                cur_x += bw + padding
            cur_y += row_h + padding

    def _paint_read_tab(self, painter: QPainter, x: int, y: int, w: int, h: int):
        self._book_rects = []
        books = self._books_for_tab("READ")
        use_size = self.config.get("read_tab_sizing") == "lines"

        if not books:
            painter.setPen(QColor("#444444"))
            painter.setFont(QFont("Courier New", 13))
            painter.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter,
                             "No finished books yet")
            return

        mono   = QFont("Courier New", 9)
        mono_b = QFont("Courier New", 9, QFont.Weight.Bold)
        bar_h  = 28
        pad    = 3
        cur_y  = y + pad - self.scroll

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
        painter.fillRect(rect, QColor(28, 28, 28))
        painter.fillRect(QRect(rect.x(), rect.y(), 4, rect.height()), color)

        if full:
            painter.fillRect(rect, dark)

        metar = f"N{b['notes']}B{b['bookmarks']}H{b['highlights']}"
        stars = "★" * (b["rating"] or 0) + "☆" * (5 - (b["rating"] or 0))

        painter.setPen(QColor("#ffffff"))
        painter.setFont(mono_b)
        painter.drawText(rect.x() + 12, rect.y() + 18, b["title"][:40])

        painter.setPen(QColor("#888888"))
        painter.setFont(mono)
        mid_x = rect.x() + rect.width() // 3
        painter.drawText(mid_x, rect.y() + 18, b["author"][:30])

        right_x = rect.x() + rect.width() - 220
        painter.drawText(right_x, rect.y() + 18, metar)

        painter.setPen(QColor("#ffdd00"))
        painter.drawText(rect.x() + rect.width() - 90, rect.y() + 18, stars)

        painter.setPen(QColor(255, 255, 255, 20))
        painter.drawRect(rect)

    def _paint_settings(self, painter: QPainter, x: int, y: int, w: int, h: int):
        mono   = QFont("Courier New", 10)
        mono_b = QFont("Courier New", 10, QFont.Weight.Bold)
        head   = QFont("Courier New", 10, QFont.Weight.Bold)

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
                ("background_color",      self.config.get("background_color"),
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

        cur_y   = y + 12 - self.scroll
        lh_head = 26
        lh_row  = 20
        col2    = 200
        col3    = 420
        col4    = 640

        painter.setClipRect(QRect(x, y, w, h))
        for section, rows in settings_ref:
            if cur_y > y + h: break

            # Section header
            painter.fillRect(QRect(x, cur_y, w, lh_head), QColor(28, 35, 45))
            painter.setPen(QColor("#cc8844"))
            painter.setFont(head)
            painter.drawText(x + 16, cur_y + lh_head - 6, section)
            cur_y += lh_head + 2

            painter.setFont(mono)
            for key, val, cmd, desc in rows:
                if cur_y + lh_row > y + h: break
                # Key
                painter.setPen(QColor("#ddaa55"))
                painter.setFont(mono_b)
                painter.drawText(x + 16, cur_y + lh_row - 5, key)
                # Value
                painter.setPen(QColor("#88ddaa"))
                painter.setFont(mono)
                elided_val = QFontMetrics(mono).elidedText(
                    str(val), Qt.TextElideMode.ElideRight, col3 - col2 - 10)
                painter.drawText(x + col2, cur_y + lh_row - 5, elided_val)
                # Command
                painter.setPen(QColor("#666688"))
                painter.drawText(x + col3, cur_y + lh_row - 5, cmd)
                # Description
                painter.setPen(QColor("#556655"))
                painter.drawText(x + col4, cur_y + lh_row - 5, desc)
                cur_y += lh_row

            cur_y += 8

        painter.setClipping(False)

    def _paint_tag_bar(self, painter: QPainter, x: int, y: int, w: int):
        self._tag_rects = []
        painter.fillRect(QRect(x, y, w, TAG_BAR_H), QColor(14, 14, 14))
        painter.setPen(QColor("#2a2a2a"))
        painter.drawLine(x, y, x + w, y)

        tags   = self._all_tags()
        mono_b = QFont("Courier New", 9, QFont.Weight.Bold)
        painter.setFont(mono_b)
        cx = x + 10
        ty = y + TAG_BAR_H // 2 + 5

        # "ALL" pill
        all_active = self.active_tag is None
        self._draw_tag_pill(painter, cx, y + 4, "ALL", all_active, "#336655")
        all_w = QFontMetrics(mono_b).horizontalAdvance("ALL") + 20
        self._tag_rects.append((QRect(cx, y + 4, all_w, TAG_BAR_H - 8), None))
        cx += all_w + 8

        for tag in tags:
            active = tag == self.active_tag
            pill_w = QFontMetrics(mono_b).horizontalAdvance(tag) + 20
            if cx + pill_w > w - 10: break
            self._draw_tag_pill(painter, cx, y + 4, tag, active, "#334466")
            self._tag_rects.append((QRect(cx, y + 4, pill_w, TAG_BAR_H - 8), tag))
            cx += pill_w + 8

    def _draw_tag_pill(self, painter, x, y, text, active, color_hex):
        mono_b = QFont("Courier New", 9, QFont.Weight.Bold)
        fm     = QFontMetrics(mono_b)
        pw     = fm.horizontalAdvance(text) + 20
        ph     = TAG_BAR_H - 8
        bg     = QColor(color_hex) if active else QColor(30, 30, 30)
        painter.fillRect(QRect(x, y, pw, ph), bg)
        painter.setPen(QColor("#aaccaa") if active else QColor("#446644"))
        painter.drawRect(QRect(x, y, pw, ph))
        painter.setPen(QColor("#ffffff") if active else QColor("#778877"))
        painter.setFont(mono_b)
        painter.drawText(QRect(x, y, pw, ph), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_lib_status(self, painter: QPainter, x: int, y: int, w: int):
        painter.fillRect(QRect(x, y, w, LIB_STATUS_H), QColor(14, 14, 14))
        painter.setPen(QColor("#2a2a2a"))
        painter.drawLine(x, y, x + w, y)
        painter.setPen(QColor("#446644"))
        painter.setFont(QFont("Courier New", 9))
        books = self._books_for_tab(self.tab)
        msg   = self.status_msg or (f"{len(books)} book(s)  —  Enter: command mode  —  Esc: close library  —  click to open")
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

    def keyPressEvent(self, ev: QKeyEvent):
        k = ev.key()

        if self._cmd_mode:
            if k == Qt.Key.Key_Escape:
                self._exit_command_mode()
            return

        if k == Qt.Key.Key_Escape:
            self.hide()
            return

        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if time.time() > self._cmd_cooldown:
                self._enter_command_mode()
            return

        if k == Qt.Key.Key_Down:
            self.scroll += 40; self.update()
        elif k == Qt.Key.Key_Up:
            self.scroll = max(0, self.scroll - 40); self.update()
        elif k == Qt.Key.Key_PageDown:
            self.scroll += self.height() - 160; self.update()
        elif k == Qt.Key.Key_PageUp:
            self.scroll = max(0, self.scroll - (self.height() - 160)); self.update()

    def wheelEvent(self, ev: QWheelEvent):
        delta = ev.angleDelta().y()
        if delta:
            self.scroll = max(0, self.scroll - delta // 3)
            self.update()

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.pos()

        # Tab clicks
        for rect, tab in self._tab_rects:
            if rect.contains(pos):
                self.tab    = tab
                self.scroll = 0
                self.status_msg = ""
                self._refresh_books()
                self.update()
                return

        # Tag clicks
        for rect, tag in self._tag_rects:
            if rect.contains(pos):
                self.active_tag = tag  # None = ALL
                self.scroll = 0
                self.update()
                return

        # Book clicks
        for rect, fp in self._book_rects:
            if rect.contains(pos):
                self.hide()
                self.open_book.emit(fp)
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
            # Map central widget rect to MainWindow coordinates
            tl = cw.mapTo(self, cw.rect().topLeft())
            self.library.setGeometry(tl.x(), tl.y(), cw.width(), cw.height())
            self.library._refresh_books()
            self.library.show()
            self.library.raise_()
            self.library.setFocus()
        except Exception as ex:
            self.reader.status_text = f"library error: {ex}"
            self.reader.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.library.isVisible():
            self.library.setGeometry(self.centralWidget().geometry())


def main():
    app     = QApplication(sys.argv)
    config  = Config()
    history = History()
    initial = sys.argv[1] if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else None
    window  = MainWindow(config, history, initial_file=initial)
    window.showMaximized()
    window.reader.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
