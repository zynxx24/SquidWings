---
name: aislop
description: Built-in quality gate for every digital work (web, video, image, slides) - no AI slop in the code or in the look.
triggers: website, web app, webapp, landing page, halaman web, frontend, html, css, ui, ux, desain, design, tampilan, video, animasi, animation, gambar, image, poster, logo, banner, thumbnail, ilustrasi, illustration, infografis, infographic, mockup, karya, konten, reels, tiktok, youtube, presentasi, slide
companion: build-web, design-ui, animate, buat-dokumen
tools: aislop_scan
---
# No AI slop (built in for every digital work)

AI slop is work that looks or reads as if nobody decided anything: the default the model reaches for first. It applies to code, to the visual result, and to the words in it. Ship nothing that a person would recognise as "made by an AI on autopilot".

## Code (web, scripts, the code that renders a video)

* When the work produces code, call `aislop_scan` before you say it is done and fix every `error` and every `ai-slop/*` finding in files you touched. Report the score. If aislop is not installed, say so in one line and check the same rules by hand.
* The rules it enforces, and that you follow while writing: no swallowed or broad exceptions that hide failure, no silent fallbacks, no stubs or empty functions left behind, no `console.log`/`print` debugging left in, no imports of packages the project does not declare, no hard-coded IDs or URLs that belong in config, no vague names (`data`, `handler2`, `utils`), no comments that narrate the edit ("now we add...", "updated to...").
* Delete what you tried and abandoned. One working path, not three half ones.

## The look (web pages, UI, video frames, images, slides)

* Avoid the AI defaults: purple-to-blue gradients, glassmorphism on everything, every section centred, identical rounded cards in threes, emoji used as icons, "sparkle" icons, stock-looking 3D blobs, neon glow on dark navy.
* Pick a direction from the subject and say it in one line before building (for example: "warung kopi, warm paper tones, hand-drawn accents"). Choose one accent colour, one type pairing, and a spacing scale, then hold them everywhere.
* Real content first: real headings, real numbers, real names the user gave. Never lorem ipsum, fake testimonials, invented stats, or "Trusted by 10,000+ teams".
* Video: one idea per shot, motion with a reason (ease, hold, cut on action), no endless zoom-and-fade, no stock "whoosh" on every cut, text on screen long enough to read (about 3 words per second).
* Images and image prompts: name a concrete subject, setting, light, lens or medium, and palette. Ask for natural imperfection. Avoid the tells: waxy skin, extra fingers, garbled text, symmetrical centred subject on a blurred bokeh background, oversaturated HDR. Check hands, text and edges before sharing; regenerate or crop when they fail.

## The words in it

* Write like the brand would, in the user's language. Cut filler: "Unlock", "Elevate", "Seamless", "Revolutionize", "In today's fast-paced world", "Tingkatkan pengalaman Anda".
* Short, specific sentences. A headline says what it is, not how amazing it is.

## How to think this through

Before making anything, ask: what would the obvious AI version of this look like? Name it, then do something more specific to this subject and this user. After making it, ask: could a stranger tell an AI made this on autopilot? If yes, find the tell and remove it.

## Before you hand it over

Look at the result as the user will (open the page at 360px and desktop, watch the video once through, view the image at full size) and fix anything that reads as generic. Say what you checked.
