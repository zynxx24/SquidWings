---
name: build-web
description: Build websites and web apps that look designed, move well, work on every screen, and actually run — plain HTML, React/Next/Vite, Vue, Svelte, Laravel Blade.
tasks: code, activity
triggers: web, website, situs, landing page, landing, company profile, portfolio, toko online, frontend, html, css, react, next, nextjs, vite, vue, nuxt, svelte, tailwind, halaman, page, form, javascript, typescript, blade, livewire, inertia, dashboard, admin panel
tools: style_pick, contrast_check, terminal_run, execute_command, preview_url, file_write, file_patch, read_file, aislop_scan, web_read
---
# Building for the web

You are making something a real person will open on a phone. It must **run**, **look deliberately designed**, and
**survive real use** (slow network, empty data, wrong input). Work in this order and do not skip steps.

## 0. Decide the stack in one line

| Request | Stack |
|---|---|
| Landing page, company profile, portfolio, event page, one-off promo | **One `index.html` + `style.css` + `script.js`** (no build). Tailwind via CDN only if the user asks for Tailwind. |
| Interactive app with state, routing, many screens (dashboard, kasir/POS, booking) | **Vite + React + TypeScript** (`npm create vite@latest app -- --template react-ts`) |
| SEO-heavy site with many pages, blog, server rendering | **Next.js** (`npx create-next-app@latest app --ts --eslint --app --src-dir --no-tailwind --use-npm --yes`) |
| PHP project, PPDB/sekolah, admin panel with database, the user says Laravel | **Laravel** — load the `laravel` skill (`load_skill("laravel")`) |
| The folder already has a project | **Its** stack and conventions. Read `package.json` / `composer.json` and two existing pages first. |

## 1. Design before code (this is what makes it not look AI-made)

1. Call `style_pick(medium="web", subject="<what the site is about>")`. Use its `css_tokens` (palette, checked for
   contrast), `fonts_css` (Google font pair), layout, shapes and motif **exactly**. Never fall back to purple
   gradients, centred-everything, emoji icons, or three identical cards.
2. Write the real content first: headline that says the benefit, sub-line, the 3–5 sections the visitor needs, real
   prices/menu/schedule if given. No lorem ipsum, no fake testimonials or fake numbers; leave a clearly marked
   placeholder if information is missing and say so.
3. One primary action per screen (WhatsApp order, daftar, beli, hubungi). Make it the most visible thing.
4. Landing page skeleton that works: hero (headline + sub + primary button + visual) → proof/benefits → product or
   service details → how it works / steps → FAQ → final call to action → footer with contact, map link, socials.

## 2. Build it right

- **Semantic HTML**: `header`, `nav`, `main`, `section` with headings in order, `footer`; `button` for actions,
  `a` for links, a `label` for every input, `alt` on every meaningful image. Keyboard must reach everything.
- **Responsive, mobile-first**: `<meta name="viewport" content="width=device-width, initial-scale=1">`, CSS grid and
  flex with `clamp()` type sizes, no fixed container widths, `max-width: 1200px; margin-inline: auto` for content,
  images with `width`/`height` and `object-fit: cover`. Test 360px, 768px, 1280px.
- **Type**: two families max, body 16–18px, line-height 1.5–1.7, line length 60–75ch, headings tight (`letter-spacing: -0.02em`).
- **Colour**: tokens as CSS variables on `:root`, dark mode by redefining them under `prefers-color-scheme`. Run
  `contrast_check` on text/background pairs you add.
- **Motion** (makes it feel alive, keep it calm): entrance on scroll with `IntersectionObserver` adding a class
  (`opacity` + `translateY(16px)`, 500–700ms, stagger children 60–80ms), hover lift on cards/buttons
  (`transform: translateY(-2px)`, 200ms), smooth anchor scroll, a sticky header that gains a shadow after scrolling.
  React: `framer-motion` (`npm i framer-motion`) with `motion.div` + `whileInView`. Always wrap motion in
  `@media (prefers-reduced-motion: no-preference)` or check it in JS. Never animate layout properties
  (`width`, `top`) — only `transform` and `opacity`.
- **States**: every fetch shows loading (skeleton matching the final layout), success, empty ("belum ada data, tambah
  yang pertama"), and error (what failed + a retry button). Disable submit while sending. Ignore stale responses.
- **Forms**: right `type`/`autocomplete`/`inputmode`, client validation for speed and server validation for truth,
  errors next to the field, keep what was typed. WhatsApp order links: `https://wa.me/62xxxxxxxxxx?text=` +
  `encodeURIComponent(message)`.
- **Security**: no secrets in frontend code; never put user text into `innerHTML` unsanitised; `rel="noopener"` on
  `target="_blank"` links.
- **SEO & sharing**: `<title>`, `meta description`, Open Graph (`og:title`, `og:description`, `og:image`), favicon,
  `lang="id"` for Indonesian sites.
- **Performance**: compress images (WebP, ≤ 200 KB hero), `loading="lazy"` below the fold, `font-display: swap`, no
  library for what 5 lines of JS can do.

## 3. Run it and give a link (always)

Start the server in the user's Terminal with `terminal_run` (it keeps running there):

| Stack | Command |
|---|---|
| Plain HTML | `python3 -m http.server 8090` |
| Vite | `npm install && npm run dev -- --host 127.0.0.1 --port 5173` |
| Next.js | `npm install && npm run dev -- -p 3000` |
| Laravel | `php artisan serve --host=127.0.0.1 --port=8000` (see the `laravel` skill) |

When it prints that it is listening, call `preview_url(port)` and give the user that link.

**sudo works.** Installing packages, PHP extensions, MySQL, nginx: just run the command with `sudo` (e.g.
`sudo apt-get install -y php8.3-xml`). When the owner saved the sudo password (Kredensial → Sistem) it runs
by itself; otherwise it opens in the Terminal panel and the user types the password there. Never ask for the
password in the chat.

**Behind the https preview link**: Laravel must trust the proxy (Laravel 11: `->withMiddleware(fn ($m) =>
$m->trustProxies(at: '*'))` in `bootstrap/app.php`) or its CSS links turn http. Vite dev refuses unknown hosts: set
`server: { allowedHosts: true }` in `vite.config.ts`, or `npm run build` and serve `dist/`. Django: add the host to
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`.

## 4. Verify like a user, then fix

1. The build/linter passes (`npm run build`, `npx tsc --noEmit`, `php artisan test`).
2. Open the page (preview link) and read the browser console: zero errors.
3. Check 360px wide: nothing overflows horizontally, buttons ≥ 44px tall, text readable.
4. Tab through the page: focus is visible everywhere.
5. `aislop_scan` on the files you wrote; fix what it reports (generic copy, AI-slop visuals, a11y).
6. Forms, login, uploads or stored user data → `load_skill("redhat")` and attack it before calling it done.

## Common errors and the fix

| Symptom | Fix |
|---|---|
| `EADDRINUSE` / port already in use | another port (`--port 5174`), or `fuser -k 5173/tcp` |
| `npm ERR! ERESOLVE` | `npm install --legacy-peer-deps` |
| Page has no CSS behind the preview link | relative asset paths (`./style.css`), trust proxy (Laravel), `allowedHosts` (Vite) |
| Blank React page | open the console: usually an import path or a component that throws; fix the first error first |
| Fonts look default | the `<link>` from `fonts_css` is missing in `<head>` or the family name differs from the CSS |
| Images missing after build | put them in `public/` (Vite/Next) and reference `/img/x.webp` |

## How to think this through

Picture the first thirty seconds of a visitor on a phone: what loads, what they read, what they tap, what happens
next. Get that one path working end to end in the browser before polishing anything else. Decide where each piece
of state lives before writing it. Treat every server response as slow, empty or failed until shown otherwise. A
passing build says nothing about what the page does: open it.
