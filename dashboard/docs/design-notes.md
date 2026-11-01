# Bot Briefs — design notes (2026-10-26 restyle)

Goal: make the Today list calm and scannable without losing density. CSS-only change (`briefs/static/style.css`); no markup/ids/htmx changes.

## Sources (opened)
1. WCAG 2.2 SC 1.4.3 Contrast (Minimum) — https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html — 4.5:1 normal text, 3:1 large; thresholds not rounded; placeholder/hover text counts.
2. WCAG 2.2 SC 2.5.8 Target Size (Minimum) — https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html — 24×24 CSS px minimum (or spacing); bigger recommended for touch.
3. NN/g, Data Tables: Four Major User Tasks — https://www.nngroup.com/articles/data-tables/ — human-readable first column, light borders/hover highlight to keep place; crowded unlabeled inline actions are hard to hit and distinguish.
4. GitHub Primer, Color usage — https://primer.style/foundations/color/overview — functional tokens via CSS vars, neutral scale for bg/borders/text, semantic roles (accent/success/attention/danger) with *muted* bg + fg text for subtle emphasis.
5. Radix Colors, Understanding the scale — https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale — step roles: 1–2 bg, 3–5 component/hover/active, 6 subtle separators, 7–8 interactive borders/focus, 11–12 text.
6. Linear, How we redesigned the Linear UI — https://linear.app/now/how-we-redesigned-the-linear-ui — reduce visual noise, limit accent ("chrome") in the palette for a neutral, timeless look, increase text contrast in both modes.

## Decisions
- **Palette**: neutral gray + one blue accent; semantic colors only in badges/hover. Ratios computed with the WCAG formula (`/workspace/contrast.py`).

| Token | Light | Dark |
|---|---|---|
| bg / surface / surface-alt | #f6f7f9 / #ffffff / #f1f2f4 | #111215 / #191a1e / #24262b |
| border (decorative) / border-strong (controls) | #e4e5e9 / #8b909c (3.2:1) | #2c2e34 / #6b707b (3.5:1) |
| text | #1b1d22 — 16.9:1 | #e6e7ea — 14.1:1 |
| text-muted (meta, snippets) | #555b68 — 6.8:1 (6.1 on hover bg) | #a2a6b0 — 7.1:1 (6.2 on hover bg) |
| text-faint (item numbers) | #5f6573 — 5.4:1 on bg | #9a9ea8 — 7.0:1 |
| accent (links, Send, focus) | #2952c8 — 6.7:1; white on it 6.7:1 | #86a8ff — 7.5:1; #0d1430 on it 7.8:1 |
| DUE badge fg/bg | #a8261b / #fcebe9 — 6.2:1 | #ff9b8f / #3b1f1d — 7.4:1 |
| NEW badge | #1a6b33 / #e4f3e8 — 5.7:1 | #7fd99a / #173022 — 8.3:1 |
| P/priority badge | #2952c8 / #e8eefc — 5.8:1 | #9db8ff / #1d2742 — 7.6:1 |
| queued badge | #8a4b00 / #fdf1dc — 6.1:1 | #f2c172 / #352816 — 8.6:1 |

- **Fonts**: system stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, …`) — no web font to ship offline, native feel on iPhone/Mac; Inter not needed. `font-variant-numeric: tabular-nums` on ids, dates, counts.
- **Type scale**: 12 (meta/ids) / 13 (snippets, controls) / 14 (body, titles desktop) / 15 (section titles, titles on phone) / 16 (logo). Line-height 1.45; 1.55 in the detail popup.
- **Spacing**: 4px base, tokens 4/8/12/16/24.
- **Density**: rows 40px min on desktop (was 28px), hairline separators (no zebra, no per-row boxes), hover tint; sub-items indented by padding instead of an extra border line.
- **Hierarchy**: section title 15px/650; item title 14px/600 primary text; snippet muted 13px; number muted 12px tabular.
- **Badges**: pill, borderless, tinted, sentence case ("Due 2026-10-25"); only NEW is uppercase small caps-style. Distinct, not shouty.
- **Actions**: ghost icon buttons 28px (≥24px per 2.5.8), color appears on row hover; 40px on phones/coarse pointers. Send = outlined accent (fills on hover), Queue = text button. Visible 2px `:focus-visible` ring in accent.
- **Popup**: surface bg, border-strong 1px, 8px radius, two-layer shadow, max-width min(600px, 100vw-24px); full-width on phones.
- **Modes**: `prefers-color-scheme` + `color-scheme: light dark` (native controls follow).
