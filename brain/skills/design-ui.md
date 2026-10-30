---
name: design-ui
description: Make interfaces look deliberate - hierarchy, spacing, type, color, and states.
triggers: desain, design, ui, ux, tampilan, warna, layout, cantik, bagus, modern, dashboard, landing page, styling, css
---
# Designing an interface

Generic AI output is recognisable: purple gradients, everything centred, identical rounded cards, emoji as icons. Avoid it by making real decisions.

**Start from the content.** Write the real headings and data first. Decide the one thing each screen is for, and make that the most prominent element. Never use lorem ipsum, or fake stats and testimonials.

**Hierarchy.** Each view has one primary action. Separate levels with size and weight first, then colour. Most text should be regular weight, with bold kept for what must be found quickly.

**Spacing.** Pick one scale (4, 8, 12, 16, 24, 32, 48, 64) and use nothing else. Related items sit closer together than unrelated ones. Generous space between sections does more than borders and boxes do.

**Type.** Use at most two families and about five sizes. Body text is 16px with line height around 1.5 and a line length of 60 to 75 characters. Tighten line height and letter spacing on large headings. Align numbers in tables with tabular figures.

**Colour.** Use one neutral scale, one accent, and semantic colours for success, warning, and error. Define them as variables and support dark mode by swapping values, not by inverting. Text contrast must be at least 4.5:1, and never rely on colour alone to convey meaning.

**Layout.** Left-align text by default. Use a consistent grid and align edges to it. Avoid cards inside cards. Design for 360px wide first, with no horizontal scrolling and touch targets of at least 44px.

**Every state is part of the design.** Hover, focus (visible, never removed), active, disabled, loading (a skeleton matching the final layout), empty (say what to do next), and error (say what went wrong and how to fix it, in plain language, next to the problem).

**Motion** only explains a change: 150 to 250ms, and respect `prefers-reduced-motion`.

Before finishing, check the page at 360px and at desktop width, in light and dark, and with long text and with no data.
