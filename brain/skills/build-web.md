---
name: build-web
description: Build web frontends that are correct, accessible, fast, and safe.
triggers: web, website, frontend, html, react, next, nextjs, vue, svelte, tailwind, halaman, page, form, javascript, typescript
---
# Building for the web

**Follow what is there.** Use the project's framework, component library, and conventions. On a new project, choose the simplest thing that works: plain HTML and CSS for a static page, and a framework only when there is real client state or routing.

**Semantic HTML first.** `button` for actions, `a` for navigation, `label` tied to every input, one `h1` and ordered headings, and `main`, `nav`, and `header` landmarks. This gives you keyboard support and screen reader support for free. Every interactive element must work from the keyboard. Every meaningful image needs `alt`.

**Responsive by default.** Include the viewport meta tag. Use flexbox and grid with relative units, and write mobile-first media queries. Do not use fixed widths on containers. Set `width` and `height` on images to prevent layout shift.

**State.** Keep it as close to where it is used as possible. Derive values instead of storing copies that can drift apart. Treat the URL as state for filters and pages, so reload and share work.

**Data fetching.** Every request has loading, success, empty, and error states, and all four must be rendered. Disable submit while sending to prevent double submission. Cancel or ignore stale responses so an old reply cannot overwrite a newer one. Handle 401 and network failure explicitly.

**Forms.** Validate on the client for speed and on the server for truth. Show errors next to the field, keep what the user typed, and use the right `type`, `autocomplete`, and `inputmode`.

**Security.** Never put untrusted text in `innerHTML` or `dangerouslySetInnerHTML` without sanitising. Never put secrets in frontend code: anything shipped to the browser is public. Keep tokens in HttpOnly cookies where possible, and add `rel="noopener"` to external links opened in a new tab.

**Performance.** Compress and size images, use `loading="lazy"` below the fold, split large bundles, and avoid a dependency for what one line can do. Debounce search input. Virtualise very long lists.

**Verify.** Run the build and the linter. Then load the page and check the browser console for errors, narrow the window to 360px, and tab through the page with the keyboard.
