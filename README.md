# ScrollReader

A focused, keyboard-driven PDF reader with a phosphor terminal aesthetic. Line-by-line navigation, annotations, a treemap library browser, and full colour theming.

---

## Install & Run

**From source:**
```bash
pip install PyMuPDF PyQt6
python scrollreader.py
```

**Windows exe:** Download `ScrollReader.exe` from [Releases](https://github.com/ampersandwichmaker/scrollreader/releases) — portable, no install needed. Place a `fonts/` folder next to the exe to add fonts.

**Linux:** Download the tarball from [Releases](https://github.com/ampersandwichmaker/scrollreader/releases) and run the binary.

---

## Navigation

| Key | Action |
|-----|--------|
| `Space` / `↓` / `S` / `Enter` | Advance one line |
| `↑` / `Tab` / `Backspace` / `W` | Back one line |
| `→` / `D` / `PageDown` | Page forward |
| `←` / `A` / `PageUp` | Page back |
| `gg` | Jump to top of document |
| `G` | Jump to bottom of document |
| `=` | Undo last move (50-step history) |
| `F11` | Toggle fullscreen |

**Open command bar:** `Ctrl+Space` or `Ctrl+Enter`

---

## Mode Keys

These work from any screen (reading, library, panels). None work while the command bar is open.

| Key | Action |
|-----|--------|
| `L` | Toggle library |
| `N` | Toggle notes panel |
| `B` | Toggle bookmarks panel |
| `H` | Toggle highlights panel |
| `I` | Toggle PDF colour inversion (per-book, remembered) |
| `?` | Open help / command reference |

Pressing the key for the currently active panel returns to reading mode.

---

## Commands

Type `:command` in the command bar. `↑`/`↓` cycles history. `cc` repeats the last command.

### Opening & Navigation
| Command | Description |
|---------|-------------|
| `open <path>` | Open a PDF |
| `gl <n>` | Go to line N |
| `gp <n>` | Go to page N |
| `sn <term>` | Search next |
| `sp <term>` | Search previous |
| `sf <term>` | Search first |
| `sl <term>` | Search last |
| `cc` | Repeat last command |

Use `;;phrase here;;` for multi-word search: `sn ;;eternal return;;`  
Search results show `[wrapped]` when the search loops around the document.

### Annotations
| Command | Description |
|---------|-------------|
| `nl [note]` | Add note at current line |
| `bl [note]` | Add bookmark |
| `hl <range> [note]` | Highlight lines (`hl 5` or `hl 3-8`) |
| `vn` / `vb` / `vh` | View notes / bookmarks / highlights panel |
| `e` | Export all annotations to Markdown |

### Book Metadata
| Command | Description |
|---------|-------------|
| `setm title <title>` | Set book title |
| `setm author <author>` | Set author |
| `bs status reading` | Set status: reading / unread / read / abandoned |
| `bs rating 4` | Set rating 1–5 |
| `fav` / `unfav` | Add/remove from favourites |

### Display
| Command | Description |
|---------|-------------|
| `zoom fit-width` | Zoom mode (fit-width / fit-page / 50% / 75% / 100%) |
| `ms` / `swapmargin` | Swap margin side (left ↔ right) |
| `set midpoint 0.42` | Reading indicator position (0–1) |
| `set page_gap 30` | Gap between pages in pixels |
| `fliplib` | Toggle library sizing mode |

### Library
| Command | Description |
|---------|-------------|
| `lib` | Open library browser |
| `set library_dir <path>` | Set library scan folder |
| `set library_recursive true` | Scan subdirectories |

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+K` / `Ctrl+L` | Cycle colour swatch backward / forward |
| `Ctrl+O` / `Ctrl+P` | Cycle indicator (main line) colour through HSV wheel |
| `Ctrl+[` / `Ctrl+]` | Highlight band height ±2px |
| `Ctrl+=` / `Ctrl+-` | UI border width ±1px |
| `Ctrl+;` / `Ctrl+'` | UI font size ±1 |
| `Ctrl+,` / `Ctrl+.` | Cycle font backward / forward |
| `Ctrl+E` / `Ctrl+R` | Help panel column offset ±20px |
| `Ctrl+F` | Flip library sizing mode |
| `F11` | Toggle fullscreen |

---

## Library Browser

Open with `L`. Close with `Esc`, `Tab`, or `L` again.

- **Treemap** — blocks sized by unread pages (or pages read in flip mode).
- **Navigate** — `↑`/`↓`/`W`/`S` move cursor. `←`/`→`/`A`/`D` cycle tabs.
- **Open** — `Space` or `Enter` on highlighted book.
- **Overflow** — books too small to show → `+12/8U2R1D1A` cell (U=unread, R=reading, D=done, A=abandoned). Select to drill down.
- **Flip mode** — `Ctrl+F` or `fliplib`: sizes blocks by pages *read* instead of pages *remaining*.
- **Tabs** — EDIT META · SEARCH · FAVORITES · SETTINGS · READING · READ · UNREAD · ABANDONED

---

## Colour Themes

`Ctrl+K` / `Ctrl+L` cycles through 15 swatches. `Ctrl+O` / `Ctrl+P` cycles the indicator (main line) colour through the full HSV wheel.

| Swatch | Feel |
|--------|------|
| `amber` | Classic warm phosphor (default) |
| `phosphor` | Green terminal |
| `cyan` | Teal CRT |
| `blood` | Red phosphor |
| `ice` | Cold blue night |
| `paper` | Warm daylight, inverted |
| `slate` | Muted blue-grey |
| `gold` | High contrast gold |
| `blue_lcd` | White on deep blue |
| `green_lcd` | Black on acid yellow-green |
| `mono_dark` | White on black |
| `mono_light` | Black on white |
| `ember` | Dark amber on white |
| `rose` | Pink on near-white |
| `lcd` | Pale yellow-green on black |

---

## Fonts

Ships with 22 fonts from [VileR's Oldschool PC Font Pack](https://int10h.org/oldschool-pc-fonts/) (CC BY-SA 4.0). Cycle with `Ctrl+,` / `Ctrl+.`.

**Adding your own:** Drop any `.ttf`, `.otf`, or `.otb` file into a `fonts/` folder next to `scrollreader.py` (or `ScrollReader.exe`).

See [`fonts/ATTRIBUTION.md`](fonts/ATTRIBUTION.md) for licence details.

---

## METAR Notation

`N5B2H45R35P328`

| Code | Meaning |
|------|---------|
| `N` | Notes |
| `B` | Bookmarks |
| `H` | Highlights |
| `R` | % read |
| `P` | Total pages |

Overflow cells show a category summary: `+12/8U2R1D1A`

---

## PDF Inversion

Press `I` to invert page colours. State is remembered per book.  
With `preload_inverted true` (default), toggling is instant after background rendering completes.

---

## Config

Stored in `~/.scrollreader/config.json`. Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `reopen_last` | `true` | Reopen last book on launch |
| `midpoint` | `0.42` | Indicator lock position (0–1) |
| `zoom_mode` | `fit-width` | Default zoom |
| `page_gap` | `30` | Gap between pages (px) |
| `margin_side` | `right` | Annotation margin side |
| `preload_inverted` | `true` | Pre-render inverted page cache |
| `library_dir` | (exe dir / ~/library) | Library scan root |
| `library_flip_mode` | `false` | Size blocks by pages read |
| `current_swatch` | `amber` | Active colour swatch |
| `ui_font_offset` | `0` | UI font size offset |
| `ui_border_width` | `2` | UI border thickness (px) |
| `help_col_offset` | `0` | Help panel column offset |

---

## Licence

MIT — see [LICENSE](LICENSE)
