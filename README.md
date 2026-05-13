# ScrollReader

A focused, keyboard-driven PDF reader with line-by-line navigation, annotations, and export.

ScrollReader keeps you in the text. A red indicator marks your current line and locks to the middle of the screen as you read — the document scrolls beneath it rather than the other way around. Everything is driven from a vim-style command bar: navigate, annotate, highlight, bookmark, export to Markdown, and print, all without leaving the keyboard.

---

## Installation

**Requirements:** Python 3.10+, and a PDF to read.

```bash
pip install PyMuPDF PyQt6
# Optional: for print support
pip install PyQt6  # PyQt6.QtPrintSupport is included in most PyQt6 distributions
```

**Run:**

```bash
python3 scrollreader.py
# or open a file directly:
python3 scrollreader.py ~/books/mybook.pdf
```

ScrollReader remembers your position in every book you open. If `reopen_last` is enabled (default), it reopens your last book automatically on launch.

---

## How It Works

- **Space** or **↓** advances one line. **↑**, **Backspace**, or **Tab** go back.
- The red `▶` indicator starts at the top of the screen and drifts down to a configurable midpoint (default ~42% from top). Once there, it stays fixed and the document scrolls under it — so your reading position is always in the same place on screen.
- Press **Enter** to open the command bar (`:` prompt). Type a command and press **Enter** to run it, or **Escape** to cancel.
- Amber triangles in the left margin mark bookmarks. Green dots mark notes. Blue bands mark saved highlights.

---

## Keyboard Reference

| Key | Action |
|-----|--------|
| `Space` / `↓` | Next line |
| `↑` / `Backspace` / `Tab` | Previous line |
| `Scroll wheel` | Navigate lines |
| `Page Down` | Jump one screenful forward |
| `Page Up` | Jump one screenful back |
| `Enter` | Open command mode |
| `Escape` | Close panel / cancel / exit command mode |

---

## Command Reference

Commands are entered via the command bar (press **Enter** to open). Type `help`, `man`, or `?` inside the app for the same reference rendered as a scrollable panel.

### Reading Controls

| Command | Description |
|---------|-------------|
| `Space  /  Down` | Advance one line |
| `Up  /  Backspace  /  Tab` | Go back one line |
| `Scroll wheel` | Navigate lines (one notch = one line) |
| `Page Down` | Jump one screenful forward |
| `Page Up` | Jump one screenful back |
| `Enter` | Open command mode |
| `Escape` | Close panel, cancel confirmation, or exit command mode |

### Command Mode

*Press Enter to open the command bar (shows ':' prompt). Type a command and press Enter to run it, or Escape to cancel. Commands are case-insensitive.*

### Opening Files

| Command | Description |
|---------|-------------|
| `open <path>` | Open a PDF file. Supports ~ expansion. |

### Navigation Commands

| Command | Description |
|---------|-------------|
| `gl<N>  /  gotoline<N>` | Jump to line N |
| `gp<N>  /  gotopage<N>` | Jump to page N |
| `lb[N]  /  lineback[N]` | Go back N lines (default 1) |
| `lf[N]  /  lineforward[N]` | Go forward N lines (default 1) |
| `pb[N]  /  pageback[N]` | Go back N pages (default 1) |
| `pf[N]  /  pageforward[N]` | Go forward N pages (default 1) |

### Range Syntax

Many commands accept a range specifier appended directly to the command:

| Syntax | Meaning |
|--------|---------|
| `<cmd>` | Current line or page |
| `<cmd><N>` | Line/page N (absolute) |
| `<cmd><A>-<B>` | Lines/pages A through B (absolute, A must be ≤ B) |
| `<cmd><fwd>[;<back>]` | fwd forward + back backward from current |

Examples:

| Command | Meaning |
|---------|---------|
| `hl` | Highlight current line |
| `hl5` | Highlight line 5 |
| `hl40-89` | Highlight lines 40–89 |
| `hl5;3` | Highlight fwd 5, back 3 from current line |
| `rl40` | Remove annotations at line 40 |
| `rl5;3` | Remove annotations fwd 5, back 3 from current |
| `rp4-10` | Remove annotations on pages 4–10 |

### Notes — `;;text;;`

Any annotating command accepts an optional note using the `;;` delimiter appended to the end:

```
nl;;this is interesting::
nl33;;note about line 33;;
bl;;come back to this;;
hl40-89;;key passage;;
```

### Annotation Commands

| Command | Description |
|---------|-------------|
| `nl[N][;;note;;]` | Add note at current line or line N |
| `bl[range][;;note;;]` | Bookmark a line |
| `bp[range][;;note;;]` | Bookmark a page |
| `hl[range][;;note;;]` | Highlight lines |
| `hp[range][;;note;;]` | Highlight pages |

### Annotation Panels

| Command | Description |
|---------|-------------|
| `sn  /  shownotes` | Open notes panel |
| `sb  /  showbookmarks` | Open bookmarks panel |
| `sh  /  showhighlights` | Open highlights panel |

Click any item in a panel to jump to that line. Press **Escape** to close.

### Remove Commands

All remove commands show a **y/n confirmation prompt** before executing. All accept an optional `;;reason;;` which is logged to history.

| Command | Description |
|---------|-------------|
| `rl[range][;;reason;;]` | Remove all annotations touching a line range |
| `rp[range][;;reason;;]` | Remove all annotations touching a page range |
| `rb[range][;;reason;;]` | Remove bookmarks only |
| `rn[range][;;reason;;]` | Remove notes only |
| `rh[range][;;reason;;]` | Remove highlights only |
| `removeall[;;reason;;]` | Remove ALL annotations for this book |
| `removeall+` | Wipe ALL stored data for this book |

*Partial overlap rule: if a remove range touches any part of an annotation, the entire annotation is deleted.*

### Export Commands

Exports write Markdown files. Two modes (set via `set export_mode <mode>`):

- `timestamped` — new file per export: `title_YYYYMMDD_HHMMSS.md`
- `running` — appends to `title_running.md`

Default save location: same folder as the PDF. Change with `set export_dir <path>`.

| Command | Description |
|---------|-------------|
| `e` | Export all annotations |
| `el[range]` | Export by line range |
| `ep[range]` | Export by page range |
| `eb[range]` | Export bookmarks only |
| `en[range]` | Export notes only |
| `eh[range]` | Export highlights only |

Examples: `el40-89`, `ep2-5`, `eb45`, `el5;3`

### Print

| Command | Description |
|---------|-------------|
| `pd` | Open system print dialog |
| `pp[range]` | Print pages (same range syntax as everything else) |

Examples: `pp` (current page), `pp5`, `pp2-8`, `pp3;1`

*Requires `PyQt6.QtPrintSupport`.*

### Zoom

| Command | Description |
|---------|-------------|
| `zoom fit-width` | Fit page width to window (default) |
| `zoom fit-page` | Fit full page height to window |
| `zoom 50%` | Small fixed zoom |
| `zoom 75%` | Medium fixed zoom |
| `zoom 100%` | Large fixed zoom |
| `zoom cycle` | Step through zoom modes in order |

### Book Metadata

| Command | Description |
|---------|-------------|
| `bookinfo` | Show title, author, status, progress, annotation counts |
| `setmeta title <value>` | Set book title |
| `setmeta author <value>` | Set author |
| `setmeta status <value>` | `unread` / `reading` / `read` / `abandoned` |
| `setmeta rating <1-5>` | Set rating |
| `setmeta tags <a,b,c>` | Set comma-separated tags |

### Per-Book Display Overrides

Use `bookset <key> <value>` to override display settings for the current book only.

| Key | Default | Description |
|-----|---------|-------------|
| `indicator_color` | `#ff4444` | Current-line indicator colour |
| `highlight_alpha` | `35` | Opacity of current-line highlight band (0–255) |
| `highlight_height` | `20` | Height in pixels of the highlight band |
| `highlight_offset` | `0` | Vertical nudge of highlight band (negative = up) |
| `saved_highlight_color` | `#4488ff` | Colour of saved highlights |
| `saved_highlight_alpha` | `45` | Opacity of saved highlights |
| `bookmark_color` | `#ffaa00` | Colour of bookmark margin markers |
| `note_color` | `#44ff88` | Colour of note margin dots |

### Global Config

Use `set <key> <value>` to change global settings (saved to `~/.scrollreader/config.json`). Use `showconfig` to print all current values.

| Key | Default | Description |
|-----|---------|-------------|
| `reopen_last` | `true` | Auto-reopen last book on launch |
| `midpoint` | `0.42` | Screen fraction where indicator locks (0.0–1.0) |
| `zoom_mode` | `fit-width` | Default zoom mode |
| `background_color` | `#1a1a1a` | Background colour |
| `statusbar_color` | `#111111` | Status bar background |
| `statusbar_text_color` | `#888888` | Status bar text colour |
| `page_gap` | `30` | Pixel gap between PDF pages |
| `export_dir` | *(empty)* | Export directory (empty = same folder as PDF) |
| `export_mode` | `timestamped` | `timestamped` or `running` |

### Data Files

ScrollReader stores its data in plain JSON — safe to edit in any text editor.

| File | Contents |
|------|----------|
| `~/.scrollreader/config.json` | Global configuration |
| `~/.scrollreader/history.json` | Per-book reading position, annotations, and metadata |

---

## License

MIT — see [LICENSE](LICENSE).
