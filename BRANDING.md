# Corella — Branding & Style Guide

This is the single source of truth for Corella's visual identity and UI conventions. If you're adding or changing anything in `web/`, this doc is what your work should match — and if a change here means the tokens below are no longer accurate, update this file in the same commit as the code.

The two places these tokens are actually implemented are `web/tailwind.config.js` (design tokens) and `web/src/index.css` (base styles + shared component classes). This document explains *why* they're set the way they are and how to use them consistently; it doesn't replace them as the source of code-level truth.

## Visual direction

Corella's UI is inspired by Harvey's product design language: quiet, editorial, professional-but-approachable. In practice that means:

- **Near-white and charcoal surfaces**, not stark white/black.
- **One strong accent color** (a deep navy) — no secondary "brand" palette, no gradients.
- **Borders over shadows.** Cards are defined by a 1px border and a barely-there shadow, not drop shadows or elevation layers.
- **A serif for headlines, a sans for everything else.** The serif signals "considered document" (report titles, page headers); the sans stays out of the way for UI chrome and body text.
- **Generous whitespace, small type.** Most UI text is `text-sm` or smaller. Let spacing do the work of hierarchy, not size.

What to avoid: stock photography, icon packs used decoratively, marketing-style copy (exclamation points, "supercharge", "unlock"), heavy shadows/glows, more than one accent color doing the same job.

## Logo

- Files: `web/src/assets/logo-light.svg` (for light backgrounds) and `logo-dark.svg` (for dark backgrounds) — always pair them behind a `dark:` media/class swap, never pick one statically. See `web/src/components/AppShell.tsx` for the reference implementation.
- Favicons: `web/public/favicon-light.svg` / `favicon-dark.svg`, wired via two `<link rel="icon">` tags with `media="(prefers-color-scheme: dark)"` on the dark one (see `web/index.html`).
- Don't recolor, rotate, stretch, or add effects (drop shadow, outline) to the mark. Don't place it on a busy or low-contrast background — it's designed to sit on the surface tokens below.
- Minimum display size: 20px tall. Below that, drop the wordmark and use the mark alone if you need something smaller (e.g. a browser tab).

## Color

All colors are Tailwind theme tokens (`web/tailwind.config.js`) — reference them by name (`bg-surface`, `text-ink-muted`, …), never hardcode a hex value in a component.

### Surfaces

| Token | Light | Dark | Use |
|---|---|---|---|
| `surface` | `#FAFAF9` | `surface-dark` `#12141A` | Page background |
| `surface-raised` | `#FFFFFF` | `surface-dark-raised` `#181B23` | Cards, inputs — anything sitting *on* the page background |

### Text ("ink")

| Token | Light | Dark (`ink-inverted`) | Use |
|---|---|---|---|
| `ink` | `#12141A` | `#FAFAF9` | Primary text |
| `ink-muted` | `#5B5F6B` | — (use `ink-inverted` + opacity, or `ink-subtle` on dark, judged per-case) | Secondary text, labels |
| `ink-subtle` | `#8B8F99` | — | Tertiary text — hints, metadata, placeholder-weight copy |

### Border & accent

| Token | Value | Use |
|---|---|---|
| `border` | `#E4E4E1` (`border-dark` `#262A34`) | The only structural line weight in the app — card outlines, dividers, input borders |
| `accent` | `#0B1B33` | The **one** brand color — primary buttons, active nav state, links |
| `accent-hover` | `#152847` | Hover state for anything using `accent` |
| `accent-foreground` | `#FAFAF9` | Text/icons placed on top of an `accent` background |

### Status (state only — never decorative)

| Token | Value | Meaning |
|---|---|---|
| `status-success` | `#1F7A4D` | Connected / enrolled / healthy |
| `status-warning` | `#9A6300` | Needs attention, not yet broken |
| `status-danger` | `#B3261E` | Errors, destructive actions, disconnected |

These three exist purely to communicate state (a connection badge, an error message, a "remove" hover). They're not a secondary decorative palette — don't reach for `status-success` green just because you want a UI element to feel "positive."

### Dark mode

Tailwind's `class` strategy (`darkMode: "class"` in the config) — every component pairs a light-mode class with a `dark:` variant of its dark-mode counterpart (e.g. `bg-surface dark:bg-surface-dark`, `text-ink dark:text-ink-inverted`). Both palettes are first-class; when you add a new surface/text color, add its dark pairing in the same change, not as a follow-up.

## Typography

Loaded via Google Fonts `<link>` tags in `web/index.html` (deliberately not a CSS `@import` — see the comment there on why: it lets the browser start the font fetch in parallel instead of discovering it mid-stylesheet-parse, which otherwise showed up as a visible font-swap flash on load).

- **Inter** (400, 500, 600) — `font-sans`, the default. Every UI element: nav, buttons, body copy, form fields, table/list text.
- **Newsreader** (optical size 6–72, weights 400/500) — `font-serif`. Reserved for headline-weight moments only: page titles (`<h1>`), card section headers (`<h2>`), the app wordmark next to the logo, and report titles/summaries in meeting output. Never use it for body text, buttons, or form labels — it's a signal, and it stops being one if it's everywhere.

Practical scale already in use across the app — reuse these rather than picking new arbitrary sizes:

| Class | Where |
|---|---|
| `font-serif text-2xl` | Page-level `<h1>` |
| `font-serif text-lg` | Card/section `<h2>` |
| `font-serif text-base` | Smaller in-card headers (e.g. a sidebar panel title) |
| `text-sm` | Default body/UI text — the large majority of text in the app |
| `text-xs` | Metadata, badges, hints, timestamps |

## Shape, spacing, elevation

- **Radius**: `rounded` (8px) is the default for cards, buttons, inputs. `rounded-sm` (6px) for small elements like status badges. `rounded-lg` (12px) is available for anything intentionally larger/softer but is rarely needed.
- **Shadow**: exactly one, `shadow-card` (`0 1px 2px 0 rgb(0 0 0 / 0.04)`) — just enough to lift a card off the page. Don't add a second, heavier shadow for "emphasis"; use the border and spacing instead.
- **Page layout**: a single centered column, `mx-auto max-w-5xl px-6`, header (`py-4`) then main content (`py-10`). Don't introduce a second container width elsewhere in the app without a real reason.
- **Card rhythm**: stacked sections use `<section className="card p-6">`, each subsequent one adding `mt-6` — see `web/src/routes/Settings.tsx` for the canonical example of several stacked cards.

## Shared components

These are the actual reusable classes defined in `web/src/index.css` (`@layer components`). Use them instead of rebuilding button/input/card styles inline — if you find yourself writing out `rounded border border-border bg-surface-raised …` by hand, it should probably be `.card` or `.field`.

- **`.card`** — the base container for every section: bordered, raised surface, `shadow-card`, dark-mode pair included.
- **`.btn-primary`** — solid `accent` background, for the one primary action in a given context (e.g. "Save", "Stop" on a live call).
- **`.btn-secondary`** — bordered, transparent background, for every other action (secondary confirmations, "Cancel", per-row actions).
- **`.field`** — the standard text input/select styling, with an `accent`-colored focus border.
- **`.label`** — small muted label text above a field (`text-sm font-medium text-ink-muted`).

### Status badges

Not yet promoted to a shared class, but used consistently as an inline pattern across Settings/Admin/Dashboard — copy this shape rather than inventing a new badge style:

```tsx
<span className={`rounded-sm border px-2 py-0.5 text-xs ${
  connected
    ? "border-status-success/30 text-status-success"
    : "border-border text-ink-subtle dark:border-border-dark"
}`}>
  {label}
</span>
```

### Navigation

Active vs. inactive nav items follow one rule: active gets a filled `accent` pill (`bg-accent text-accent-foreground`), inactive gets muted text with a subtle hover background (`hover:bg-black/[0.03] dark:hover:bg-white/[0.04]`). See `AppShell.tsx`.

## Iconography

No icon library is installed. If a screen genuinely needs icons, keep them stroke-based and single-color (`currentColor`, inheriting `ink`/`accent` — never a multi-color icon set), and raise it as a real decision (which library, why) rather than adding one ad hoc for a single use.

## Voice & copy

- Plain and specific over clever. "Couldn't save — check your connection" beats "Oops! Something went wrong ✨".
- No exclamation points in UI copy. No "supercharge"/"unlock"/"seamless"-style marketing language anywhere in the product, including empty states and error messages.
- Error messages say what happened and, where possible, what to do about it — see `ApiError` handling throughout `web/src/routes/*.tsx` for the established pattern (`err instanceof ApiError ? err.message : "Couldn't <verb>"`).
- Status/badge text is short and factual: "Connected via .env", "Not connected", "Saved ✓" — not "You're all set!".

## Checklist for new UI

Before opening a PR that touches `web/`:

- [ ] Every color comes from a Tailwind theme token, not a hardcoded hex.
- [ ] Every color/surface class has a `dark:` pairing.
- [ ] Headlines use `font-serif`; everything else uses the default `font-sans`.
- [ ] New containers use `.card`, not a hand-rolled border/shadow.
- [ ] New buttons use `.btn-primary`/`.btn-secondary`, not one-off styling.
- [ ] Copy is plain, specific, and exclamation-point-free.
- [ ] If you added a new token or component class, this file is updated in the same change.
