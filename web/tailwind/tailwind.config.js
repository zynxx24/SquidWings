// Tailwind for the soft card layer (web/static/css/soft.css). Colours are the existing tokens in
// tokens.css, so nothing changes hue; Tailwind only supplies radius, shadow, spacing and motion.
// Preflight is off: app.css, trading.css and router.css keep their own base styles.
//
// Rebuild after editing web/tailwind/soft.css or adding tw classes to a page:
//   npx tailwindcss@3.4.17 -c web/tailwind/tailwind.config.js -i web/tailwind/soft.css -o web/static/css/soft.css --minify
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./web/static/**/*.html', './web/static/js/app.js', './web/static/trading/*.js', './web/static/router/*.js'],
  // tw- prefix: page class names such as .grid, .inline or .hidden must never pick up a Tailwind utility.
  prefix: 'tw-',
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        surface: 'var(--surface)', 'surface-2': 'var(--surface-2)', page: 'var(--bg)',
        line: 'var(--line)', 'line-strong': 'var(--line-strong)', ink: 'var(--text)', muted: 'var(--muted)',
        faint: 'var(--faint)', accent: 'var(--accent)', 'accent-soft': 'var(--accent-soft)',
      },
      borderRadius: { card: '16px', soft: '12px' },
      boxShadow: {
        soft: 'var(--soft-shadow)',
        lift: 'var(--soft-shadow-lift)',
        ring: 'var(--soft-ring)',
      },
      transitionTimingFunction: { soft: 'cubic-bezier(0.2, 0.7, 0.2, 1)' },
    },
  },
};
