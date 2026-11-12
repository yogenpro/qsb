# QSB / CPM dashboard design system

Accepted by the user on 2026-11-11 after reviewing the Interface Design preview:
“looks better than before. let's keep it and save the design pattern for future.”
This is the accepted direction for future dashboard edits. The implementation is
in `dashboard/src/style.css` and `dashboard/src/main.tsx`; CSS custom properties
are the source of truth for token values. This acceptance does not imply a deployment.

## Intent and direction

Build a focused market-research workspace for finding an options strategy,
inspecting its legs, and reviewing sampled book observations. The selected
instrument and its replay are the focal point. Keep the compact header, neutral
surfaces, readable data, and consistent bid/ask treatment.

Domain: ordered option legs, strikes and expirations, depth ladders, sampled
liquidity, collection coverage, and distinct collection/source clocks.
The color world is white research paper, slate text, steel rules, teal bids,
orange asks, amber capture warnings, and red failures.

The signature is paired bid/ask marks: the brand mark, buy/sell leg labels,
depth headers, price values, and quote-range chart share the same semantic colors.
Avoid a large promotional introduction, decorative KPI cards, and ornamental
gradients or motion. Density should improve scanning without shrinking core text.

## Layout and hierarchy

- Desktop: 72px white header; compact title/date row; session-summary strip;
  instrument explorer on the left; selected instrument and observations on the right.
- Content maximum width: 1680px with 32px horizontal padding.
- Workspace: 360px explorer, flexible detail column, 24px gutter. Explorer shares
  the page canvas so it reads as navigation within the same workspace.
- Detail order: instrument definition, book replay, hourly quote history and
  coverage. Keep source-clock context beside the observation clock.
- Session metrics use one strip with subtle dividers, not separate raised cards.
- Instrument details and trade windows use native expandable disclosure controls.

## Palette and depth

Use existing semantic CSS variables rather than introducing parallel theme values.

| Role | Token | Value |
| --- | --- | --- |
| Page canvas | `--archive-canvas` | `#f5f7f9` |
| Book/panel surface | `--book-surface` | `#ffffff` |
| Inset control | `--control-inset` | `#f0f3f6` |
| Primary text | `--ink` | `#1c2a39` |
| Secondary text | `--ink-secondary` | `#526171` |
| Metadata | `--ink-tertiary` | `#637284` |
| Disabled text | `--ink-disabled` | `#88939f` |
| Standard / soft rules | `--rule` / `--rule-soft` | `#dbe2e9` / `#eaf0f4` |
| Selected instrument | `--selection` / `--selection-rule` | `#e9f3f5` / `#8db6bd` |
| Bid / bid surface | `--bid` / `--bid-wash` | `#087c78` / `#edf7f6` |
| Ask / ask surface | `--ask` / `--ask-wash` | `#b65325` / `#fcf2ec` |
| Valid capture | `--capture-valid` | `#087c78` |
| Warning / warning surface | `--capture-warning` / `--warning-wash` | `#a56814` / `#fff6e5` |
| Failure / failure surface | `--failure` / `--failure-wash` | `#a63b36` / `#fff1ef` |
| Keyboard focus | `--focus` | `#286cbb` |
| Regular / extended session | `--session-regular` / `--session-extended` | `#f0f6f8` / `#fcf7ee` |

Depth is borders and quiet surface shifts, without decorative shadows. Panels
use a 1px standard rule and 8px radius; controls and instrument rows use 6px;
status badges use 4px. Inputs use the inset surface. Selected instruments add
a 3px teal left rule; the definition panel repeats the 3px teal top rule.

## Typography and spacing

- UI stack: `Segoe UI`, `Helvetica Neue`, Arial, sans-serif.
- Data stack: `SFMono-Regular`, Consolas, `Liberation Mono`, monospace.
- Base: 16px / 1.5; tabular numerals throughout. Keep prices and times aligned.
- Approximate 1.2 scale with role-specific data sizes: title 32px/650,
  selected symbol 28px, metrics 26px/600, section headings 20px/650,
  instrument symbols 17px/600, table values 15px, strategy labels 14px,
  secondary labels and leg rows 13px, expiry/tertiary metadata 12px.
- Use weight and text tone as well as size to distinguish values from labels.
  Preserve the user's preference for readable instrument text; avoid reducing
  essential data sizes to fit additional content.
- Base spacing unit: 4px. Use 4/8px for micro gaps, 12/16px within controls and
  rows, 20/24px within panels, and 24/32px between major regions.

## Reusable component patterns

- **Button:** minimum 44px height; 8px 12px padding; 6px radius. Compact segmented
  book tabs and partition selectors are 40px high. Use native controls.
- **Focus:** 3px `--focus` outline, offset 3px. Include a skip link to instruments.
- **Instrument row:** 16px padding, 8px separation, symbol/type header,
  strategy label, aligned leg rows, and a ruled metadata footer. Each leg shows
  side/ratio, root/strike, and expiry. Expose selection with `aria-pressed`.
- **Filters:** book segments, text search, strategy and expiration selects.
  Strategy and expiration share a two-column grid where space permits.
- **Collection date:** DayPicker calendar in a Radix popover, aligned to the date
  button. Use the existing inset control, white panel, teal selection and focus
  tokens. Calendar cells are 44px (40px wide at 360px and below), with 12px panel
  padding. Month/year dropdowns use the same native select styling as instrument
  filters: inset fill, 1px rule, 6px radius, 44px height and regular-weight text.
  Month/year dropdowns and arrow-key navigation select available Eastern
  collection dates; other dates are disabled. Close on selection or Escape and
  return focus to the trigger.
- **Replay:** collection clock and published source clock remain distinct;
  labeled status badge; dark play/pause action; native range input; previous/next
  controls; numeric observation counter.
- **Book ladder:** paired bid/ask tables with semantic header fills and 2px
  colored rules; right-aligned prices; labels and colors identify each side.
- **Status:** quoted = teal, valid empty = amber, failed = red. Always include
  text; color alone must not carry the meaning.
- **Charts:** preserve missing-hour space, regular/extended-session shading,
  bid/ask colors, coverage gaps, and legible axis labels. Wide charts scroll
  within their panel rather than compressing labels or widening the page.

## Responsive behavior and data integrity

At 1200px reduce page padding to 24px and explorer width to 320px; at 900px use
280px and stack controls/ladders as needed. At 720px switch to one column with
16px page padding and a 28px title. Bound the mobile instrument list to 300px
so the selected instrument remains reachable. At 360px stack dates and ladders.
Keep the page free of horizontal overflow; allow bounded table/chart scrolling.

Preserve Eastern Time with daylight saving, strategy and any-leg expiry filters,
exact strike/price strings, and visible leg definitions. Keep valid empty books,
failed attempts, and missing observations distinct. Do not carry a previous book
across capture failures or treat sampled observations as a continuous event feed.
Keep synthetic data explicitly labeled. Preserve loading, unavailable, empty,
disabled, selected, hover, active, and keyboard-focus states.

## Future changes and validation

Read this file before dashboard UI work. Reuse its tokens and component patterns;
update it when a subsequent user decision changes the direction. Keep unrelated
collector and publication behavior outside visual changes.

For UI changes, run the dashboard build and existing tests, then inspect the
rendered result at desktop and mobile widths. Check intermediate widths when
layout changes. The accepted implementation passed build, 18 automated tests,
browser smoke checks, and overflow checks at 320, 390, 768, 1024, and 1440px.
That historical verification is not a substitute for checking subsequent edits.
