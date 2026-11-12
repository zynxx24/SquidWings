---
name: aislop
description: Built-in quality gate for every digital work (web, video, image, slides, documents) - a committed look that differs from chat to chat, and no AI slop in the code, the look, or the words.
triggers: website, web app, webapp, landing page, halaman web, frontend, html, css, ui, ux, desain, design, tampilan, video, animasi, animation, gambar, image, poster, logo, banner, thumbnail, ilustrasi, illustration, infografis, infographic, mockup, karya, konten, reels, tiktok, youtube, presentasi, slide, pptx, flyer, brosur, undangan
companion: build-web, design-ui, animate, buat-dokumen
tools: aislop_scan, style_pick
---
# No AI slop (built in for every digital work)

AI slop is work nobody decided anything about: the first default a model reaches for, which is also every other model's default. Ship nothing a person would recognise as "an AI made this on autopilot".

## 1. Pick a direction first, a different one each time

* Before any visual work, call `style_pick(medium, subject)`. It returns one direction (palette with hex roles, fonts, layout, motifs, motion, what to avoid) that this chat and recent chats have not used. Say it in one line ("Arah: Risograph dua warna, cocok untuk poster gig"), then build exactly that: only its colours, only its fonts.
* The user says "beda", "ganti gaya", "jangan sama": call it again with `avoid` set to the last style. They name a look: use theirs and drop the pick.
* Files of one set (a report and its slides, a poster and its feed post) share one style on purpose: pass `style` with the earlier id.
* Slides and Word files: pass the pick's `office_theme` as `theme`. Web, video and SVG: load the pick's `fonts_css`.

## 2. The tells, by medium (find them and remove them)

* **Web:** centred hero + three identical feature cards + testimonial row + CTA band (the template layout); purple-to-blue gradients; glassmorphism; Inter or Poppins by default; emoji or sparkle icons; every section the same height; "Get started" and "Learn more" twins; dark navy with neon glow; stock 3D blobs.
* **Slides:** every slide a title plus three bullets; the same layout twenty times; clip-art icons in circles; an "Agenda" slide nobody needs; "Thank you!" or "Any questions?" as the last slide; text that fills the slide edge to edge. Vary the layout with the content: a number slide, a quote slide, a two-column comparison, one big picture.
* **Pictures (SVG, HTML, image prompts):** one symmetric object centred on a blurred or gradient background; glossy 3D; fake text; perfect geometric repetition; neon outlines. Draw with the pick's motifs and texture (grain, halftone, brush, paper). For a prompt, name a concrete subject, setting, light, lens or medium, palette, and a natural imperfection. Check hands, text and edges.
* **Video:** endless zoom-and-fade; a whoosh on every cut; text gone before it can be read (keep about 3 words per second); motion with no reason. One idea per shot, cut on action, hold the payoff.
* **Code (web, scripts, the code that renders a video):** call `aislop_scan` before you say it is done and fix every `error` and `ai-slop/*` finding in files you touched; report the score. No swallowed or broad exceptions, silent fallbacks, stubs, debug prints, undeclared imports, hard-coded IDs or URLs that belong in config, vague names (`data`, `handler2`), comments that narrate the edit. Delete abandoned attempts. If aislop is not installed, say so in one line and check the same rules by hand.

## 3. The words

* Real content only: real headings, names and numbers the user gave. Never lorem ipsum, invented stats, fake testimonials, "Trusted by 10,000+ teams", fake logos.
* Cut filler in both languages: "Unlock", "Elevate", "Seamless", "Revolutionize", "In today's fast-paced world", "Tingkatkan pengalaman Anda", "Di era digital ini", "Solusi terbaik untuk Anda", "Mari kita", "Tentunya", "Dalam dunia yang serba cepat".
* A headline says what the thing is or does, in the brand's own voice and the user's language. Short, specific sentences.

## How to think this through

Before making anything, ask: what would the obvious AI version of this look like? Name it, then do something more specific to this subject, this place and this user, inside the direction you picked. After making it, ask: could a stranger tell an AI made this on autopilot? If yes, find the tell and remove it.

## 4. Before you hand it over

* **The swap test:** replace the brand name with a competitor's. If nothing else would need to change, it is still generic. Add what only this subject has: its place, its product, its numbers, its people.
* **Look at it as the user will:** open the page at 360px and desktop, render the slides or the SVG to an image and view it, watch the video once through. Name the one tell you found and fixed, and say what you checked.
