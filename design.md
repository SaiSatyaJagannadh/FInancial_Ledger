# Design: Personal Ledger

The locked system. Every Hallmark run reads this first and pages share it. Change it on purpose; the file is the rule.

## System
- Genre: editorial (inferred; a household's ledger)
- Shape of the first page: app screen
- Theme: brand, the khata (a page ruled in two inks)
- Axes: light/roman-serif/cool · posture: restrained

## Tokens
`ledger/styles/tokens.css` is the source of truth, and `.streamlit/config.toml` repeats its colours because Streamlit cannot read a CSS variable (`tests/test_theme.py` holds the two together). Hex rather than OKLCH for that reason. The roles:
```css
:root {
  --color-paper: #F7F6F2;  --color-paper-2: #EDEBE3;  --color-sheet: #FFFDF9;
  --color-ink: #16202E;  --color-ink-2: #3B4756;  --color-muted: #5E6774;
  --color-out: #B3261E;  --color-back: #1B7A5A;
  --color-rule: #DCD8CC;  --color-edge: #8A8F96;
  --color-accent: #1B3A5C;  --color-accent-ink: #FFFDF9;  --color-focus: #0B4FB0;  --color-selected: #E3E8EE;
  --font-display: "Newsreader", Georgia, ui-serif, serif;
  --font-body: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  --radius-card: 4px;
}
```
The spacing scale, the type scale, the easings and the durations live in `tokens.css`.

**Out and back mean the direction of money and nothing else.** Red and green are never a person's chart series, never decoration.

**Faces were chosen for the rupee.** Both have a real ₹ glyph and digits of equal width. Check both before swapping either: Instrument Sans and Karla, for two, have no ₹ at all.

## The ruled page
- A double rule in ink under every page title.
- Rows are lines on the page: a hairline on the row itself, no box around it. Expanders are rows too.
- One figure leads, the rest stand beside it parted by hairlines. Never equal tiles.
- `ui.ruled_table` for any table a person reads. On a phone each row restacks as labelled blocks.
- A form sits beside a slip saying exactly what Save will write, with Save inside the slip. The slip is a tinted well, so nothing inside it draws a box of its own.
- On a phone a row keeps its amount and date on one line. Each kind of row has its layout in `ledger.css`, under the keys `khata-rows-entry`, `-spend`, `-clear`, `-charge` and `-line`.

## Controls
- Primary: ink-blue fill · 4px · 40px, 44px on a coarse pointer. One to a screen.
- Secondary: outlined in `--color-edge`, same radius and height.
- Row actions (Edit, Delete, Restore, Cancel): tertiary, text only. The armed "Delete it" is the one primary in a row.
- A choice between a few: `st.radio(horizontal=True)`, drawn as labels you press. Selected is a tint with an ink edge, never the primary's fill: a state must not look like an action.

## Motion
- Character: still. A 1px press on buttons, nothing else; under reduced motion everything ends at once.

## Exports
`ledger/styles/tokens.css` in this project. Tailwind, DTCG or shadcn blocks are added here on request.
