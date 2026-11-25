# Themes: Beginner → Advanced (ship T1 first; add others as CSS-variable files)
Every theme is one file, `static/themes/<name>.css`, defining the same tokens. The user picks a theme in Settings; the admin sets the default.
Shared type: **Source Serif 4** (display H1/H2), **Inter** (UI, 14/16px body), **JetBrains Mono** (KPIs, code). Icons: **Lucide**.
All pairs must pass a WCAG AA contrast check (P4 runs it automatically).

## T1 — "Clinical Clean" (Beginner · default)
Light and calm, with a trusted-healthcare feel. White cards on a cool-gray canvas and a single accent.
```css
--bg:#F7F9FC; --surface:#FFFFFF; --text:#0F172A; --muted:#64748B; --border:#E2E8F0;
--primary:#1D4ED8; --accent:#0EA5A4; --success:#15803D; --warning:#B45309; --danger:#B91C1C;
--chart: #1D4ED8,#0EA5A4,#7C3AED,#F59E0B,#64748B;
```
Build skills: Tailwind basics, cards, a KPI tile component.

## T2 — "Executive Slate" (Intermediate)
Boardroom: a navy header band, slate neutrals, and gold highlights for "winning" metrics. Serif headlines.
```css
--bg:#F4F5F7; --surface:#FFFFFF; --text:#111827; --muted:#6B7280; --border:#D1D5DB;
--primary:#0B2545; --accent:#C9A227; --success:#166534; --warning:#A16207; --danger:#991B1B;
--chart: #0B2545,#13315C,#8DA9C4,#C9A227,#6B7280;
```
Build skills: layout grid, sticky header band, data tables with sticky columns.

## T3 — "Precision Pharma" (Advanced)
A modern life-sciences brand look: indigo-to-teal gradient hero, glass-effect KPI cards (with a solid-color fallback), molecular-line SVG illustrations.
```css
--bg:#F5F7FF; --surface:rgba(255,255,255,.85); --text:#0B1026; --muted:#5B6478; --border:#DDE3F5;
--primary:#4338CA; --accent:#0D9488; --success:#059669; --warning:#D97706; --danger:#DC2626;
--gradient: linear-gradient(135deg,#4338CA 0%,#0D9488 100%);
--chart: #4338CA,#0D9488,#9333EA,#F59E0B,#0EA5E9;
```
Build skills: backdrop-filter with fallback, animated count-up KPIs (respect prefers-reduced-motion), SVG illustration.

## T4 — "Midnight Analytics" (Advanced · dark mode)
A command-center feel for heavy analytics: deep navy, neon-teal accents, monospace KPIs. Pairs with T1 for light/dark switching.
```css
--bg:#0B1220; --surface:#111A2E; --text:#E5EAF5; --muted:#94A3B8; --border:#1E2A44;
--primary:#38BDF8; --accent:#2DD4BF; --success:#34D399; --warning:#FBBF24; --danger:#F87171;
--chart: #38BDF8,#2DD4BF,#A78BFA,#FBBF24,#94A3B8;
```
Build skills: a theme switcher with saved preference, ECharts theme registration per theme, a dark-safe map palette.

## Component standards (all themes)
- KPI tile: icon (Lucide) · label · big mono value · delta chip (▲/▼ with color + text, never color alone) · sparkline · source tooltip.
- Header: product wordmark "LaunchLens" + VP+AI badge; right side holds the theme toggle, quota meter, and avatar menu.
- Imagery: abstract molecules, networks, and maps (SVG you author or CC0). No stock photos of patients or clinicians, and no brand logos.
- Motion: 150–200ms ease-out; nothing auto-plays; prefers-reduced-motion is honored.
