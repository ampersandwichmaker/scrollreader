# ScrollReader

A focused, keyboard-driven PDF reader with a phosphor terminal aesthetic. Line-by-line navigation, annotations, a treemap library browser, and full colour theming.

---

## Install & Run

**From source:**
```bash
pip install PyMuPDF PyQt6
python scrollreader.py
```

**Windows exe:** Download `ScrollReader.exe` from [Releases](https://github.com/ampersandwichmaker/scrollreader/releases) — portable, no install needed.

**Linux:** Download the tarball from [Releases](https://github.com/ampersandwichmaker/scrollreader/releases) and run the binary.

---

## Navigation

| Key | Action |
|-----|--------|
| `Space` / `↓` / `S` / `Enter` | Advance one line |
| `↑` / `Tab` / `Backspace` / `W` | Back one line |
| `→` / `D` / `PageDown` | Page forward |
| `←` / `A` / `PageUp` | Page back |
| `0` | Undo last move (50-step history) |
| `F11` | Toggle fullscreen |

**Open command bar:** `Ctrl+Enter` or `Ctrl+Space`

---

## Commands

Type `:command` in the command bar. `↑`/`↓` cycles command history.

### Opening & Navigation
| Command | Description |
|---------|-------------|
| `open <path>` | Open a PDF |
| `gl <n>` | Go to line N |
| `gp <n>` | Go to page N |
| `sn <term>` | Search next occurrence |
| `sp <term>` | Search previous |
| `sf <term>` | Search first |
| `sl <term>` | Search last |
| `cc` | Repeat last command |

Use `;;phrase here;;` for multi-word search: `sn ;;eternal return;;`

### Annotations
| Command | Description |
|---------|-------------|
| `nl [note]` | Add note at current line |
| `bl [note]` | Add bookmark |
| `hl <range> [note]` | Highlight lines (e.g. `hl 5` or `hl 3-8`) |
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
| `ms` / `swapmargin` | Swap margin side (left ↔ right) — triggers re-render |
| `set midpoint 0.42` | Reading indicator position (0–1) |
| `set page_gap 30` | Gap between pages in pixels |

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
| `Ctrl+L` | Open library |
| `Ctrl+N` | Notes panel |
| `Ctrl+B` | Bookmarks panel |
| `Ctrl+H` | Highlights panel |
| `Ctrl+O` / `Ctrl+P` | Cycle colour swatch backward / forward |
| `Ctrl+,` / `Ctrl+.` | Cycle font backward / forward |
| `Ctrl+[` / `Ctrl+]` | Highlight band height ±2px |
| `Ctrl+=` / `Ctrl+-` | UI border width ±1px |
| `Ctrl+;` / `Ctrl+'` | UI font size ±1 |
| `I` | Toggle PDF colour inversion (per-book, remembered) |
| `F11` | Toggle fullscreen |

---

## Library Browser

Open with `lib` or `Ctrl+L`. Closes with `Esc` or `Tab`.

- **Treemap** — blocks sized by unread pages. Proportional area, largest = half screen.
- **Navigate** — `↑`/`↓`/`W`/`S` move cursor between books.
- **Tabs** — `←`/`→`/`A`/`D` cycle tabs instantly.
- **Open** — `Space` or `Enter` opens the highlighted book.
- **Overflow** — books too small to show go into a `+N more` cell. Select it to drill down infinitely.
- **Tabs available** — EDIT META · SEARCH · FAVORITES · SETTINGS · READING · READ · UNREAD · ABANDONED

---

## Colour Themes

`Ctrl+O` / `Ctrl+P` cycles through 15 built-in swatches:

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
| `marathon` | White on Bungie blue |
| `neon` | Black on acid green |
| `mono_dark` | White on black |
| `mono_light` | Black on white |
| `ember` | Dark amber on white |
| `rose` | Pink on near-white |
| `lcd` | Pale yellow-green on black |

Customise any colour:
```
set theme_primary #ffbb33
set theme_bg #000000
set theme_bright #ffb000
```

---

## Fonts

Ships with a curated set of fonts. Cycle with `Ctrl+,` / `Ctrl+.`.

**Bundled fonts include:**

| Font | Source |
|------|--------|
| IBM VGA 8x16 + 21 oldschool PC variants | [int10h.org](https://int10h.org/oldschool-pc-fonts/) — CC BY-SA 4.0, VileR |

**Adding your own:** Drop any `.ttf`, `.otf`, or `.otb` file into a `fonts/` folder next to `scrollreader.py` (or `ScrollReader.exe`) and it appears in the cycle on next launch.

See [`fonts/ATTRIBUTION.md`](fonts/ATTRIBUTION.md) for full licence details.

---

## METAR Notation

Each book shows a compact status string: `N5B2H45R35P328`

| Code | Meaning |
|------|---------|
| `N` | Notes count |
| `B` | Bookmarks count |
| `H` | Highlights count |
| `R` | % read (0–100) |
| `P` | Total pages |

---

## PDF Inversion

Press `I` to invert page colours (white→black, black→white). Great for night reading with the `mono_dark` or `phosphor` swatch.

- State is remembered **per book** — reopening a book restores its inversion state.
- With `preload_inverted true` (default), the inverted cache is built in the background after normal rendering, so toggling is instant.
- Disable pre-rendering: `set preload_inverted false` (saves memory on large books).

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
| `library_dir` | `~/Downloads` | Library scan root |
| `current_swatch` | `amber` | Active colour swatch |
| `ui_font_offset` | `0` | UI font size adjustment |
| `ui_border_width` | `2` | UI border thickness (px) |

---

## Licence

MIT — see [LICENSE](LICENSE)
