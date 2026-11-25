# Vulnerability classes: tell, prove, fix

For each class: **Tell** = how it looks in code a cheap model wrote; **Prove** = the gentlest exploit that confirms it on the local app (`$T` = `http://127.0.0.1:<port>`); **Fix** = the correct pattern. Prove on the running app before you call something a finding; fix every instance of the class, not the one you found.

Order roughly by how often it is the serious bug. The first two need no grep and no payload cleverness — just reading and one extra request — and are the most common real holes.

## 1. Broken access control / IDOR / missing authorization
The number-one serious API bug, and invisible to any scanner.
- **Tell:** a handler fetches `thing/:id` or `/account/:id` and returns it after checking only that you are logged in, never that the record is *yours*. Admin routes gated by a hidden link, not a server check. `is_admin` read from a request field or JWT claim the client can set.
- **Prove:** register two users A and B. As B (B's token/cookie), request A's resource: `curl -H "Authorization: Bearer $B" "$T/api/orders/<A_order_id>"`. If B sees A's data → IDOR. Try an admin-only route with a normal user's token; try the raw endpoint of a button your role should not show.
- **Fix:** on every record access, check ownership (`where owner_id = current_user`), not just authentication. Centralise authorization (a decorator/middleware/policy), deny by default, and never trust a role/id that came from the client body or an unverified token.

## 2. Authentication & session failures
- **Tell:** passwords in plaintext or a fast hash (md5/sha1); no lockout or rate limit on login; session id that never expires or does not rotate on login (fixation); password reset token that is guessable, long-lived, or not single-use; `remember me` that never dies; JWT with a weak/hardcoded secret or `alg:none`.
- **Prove:** try 50 logins in a loop — if none are slowed or blocked, no brute-force defence. Log in, note the session cookie, log in again — if it did not change, session fixation. Decode the JWT ([base64] the middle) and check the alg; try resending a used reset link.
- **Fix:** argon2id or bcrypt for passwords; rate-limit + backoff + lockout on login and reset; rotate the session id on login and invalidate on logout; reset tokens that are random (≥128 bit), single-use, and short-lived; a strong signing secret from the environment; verify the JWT signature and pin the algorithm.

## 3. SQL / NoSQL injection
- **Tell:** `cursor.execute("SELECT ... WHERE x = '" + v + "'")`, f-strings or `.format` or `%` building SQL, a `${v}` inside a query template literal, Mongo queries taking raw request objects (`{ $gt: ... }` injection).
- **Prove:** send `' OR '1'='1` / `'--` in the parameter and watch for a SQL error, or all rows, or a boolean difference; time-based: `'; SELECT pg_sleep(5)--` (or `' OR SLEEP(5)-- `) and see if the response hangs 5s. Confirm safely on your own DB; do not run `sqlmap` against anything but your own app.
- **Fix:** parameterised queries only — placeholders (`?`, `%s`, `$1`) with the values passed separately, never concatenated. Use the ORM's safe query builder. For Mongo, cast and validate types so an object cannot arrive where a string is expected.

## 4. Command injection
- **Tell:** `os.system`, `subprocess(..., shell=True)`, `child_process.exec`, PHP `shell_exec/system/passthru`, backticks — with any part built from input (a filename, a host to ping, an archive name).
- **Prove:** in the parameter send `; id` / `| id` / `$(id)` / `` `id` `` and look for command output or side effects. Blind: `; sleep 5` and watch the delay.
- **Fix:** avoid the shell. Call the program with an argument list (`subprocess.run([...])` no `shell=True`, `execFile`/`spawn`, `escapeshellarg`). Allowlist the values; never pass raw input as a shell string.

## 5. Code injection & unsafe deserialization
- **Tell:** `eval` / `exec` / `new Function` on input; `pickle.loads`, `yaml.load` (not `safe_load`), `marshal.loads`, PHP `unserialize`, Java `ObjectInputStream`, `node-serialize` on request bytes.
- **Prove:** eval/exec — send an expression with a visible effect (`7*7` reflected as `49`, or a sleep). Deserialization — a crafted object is RCE; for your own app, confirm the sink exists and reaches untrusted bytes rather than firing a real gadget.
- **Fix:** never eval input — parse it (`json.loads`, a real parser, a small expression library with no builtins). Deserialize only trusted data; prefer JSON; if you must use a rich format, sign the bytes and verify before loading, or use `yaml.safe_load`.

## 6. Cross-site scripting (XSS)
- **Tell:** input echoed into HTML without escaping; `innerHTML`/`outerHTML`/`insertAdjacentHTML` = variable; `dangerouslySetInnerHTML`, `v-html`, `document.write`; template escaping turned off (`|safe`, `mark_safe`, Blade `{!! !!}`, Twig `|raw`, `autoescape off`); `$(el).html(x)`.
- **Prove:** reflected — put `<script>alert(1)</script>` or `"><img src=x onerror=alert(1)>` in a parameter and see it return unescaped in the HTML (view source, or check the response body with curl). Stored — save it in a field (name, comment, bio) and load the page that shows it. DOM — trace input from `location`/`postMessage` into a sink.
- **Fix:** output-encode for the context (HTML-escape by default — most template engines do if you do not disable it). Build DOM with `textContent`, not HTML strings. If you must render user HTML, sanitise with DOMPurify (client) or bleach (server). Add a Content-Security-Policy as defence in depth. Set cookies `HttpOnly` so a stolen script cannot read them.

## 7. Server-side template injection (SSTI)
- **Tell:** user input concatenated into a template string before rendering (`render_template_string("Hi " + name)`, Jinja/Twig/Handlebars/EJS built from input).
- **Prove:** send `{{7*7}}` (Jinja/Twig) or `${7*7}` / `<%= 7*7 %>` and see if `49` comes back. If it evaluates, it often escalates to RCE.
- **Fix:** never build a template from input. Pass user data as *context variables* to a fixed template; the engine escapes them.

## 8. Path traversal / arbitrary file read/write
- **Tell:** `send_file`, `res.sendFile`, `open`, `readFile`, `file_get_contents` on a path built from `req.query`/`params`/`body`; download/avatar/export endpoints.
- **Prove:** `curl "$T/download?file=../../../../etc/passwd"` (and URL-encoded `..%2f`, `....//`, a leading `/`). If you get the file, traversal.
- **Fix:** resolve the final real path and confirm it is inside the intended base directory (`os.path.realpath(base)` prefix check / `path.resolve` containment); strip or reject `..`; better, map an id to a filename from an allowlist instead of taking a path at all.

## 9. Server-side request forgery (SSRF)
- **Tell:** the server fetches a URL the user supplies (webhook, image-from-URL, link preview, PDF/screenshot of a page) with `requests`/`urllib`/`axios`/`fetch`.
- **Prove:** point it at `http://127.0.0.1:<otherport>/` or the cloud metadata address `http://169.254.169.254/latest/meta-data/` and see if the server reaches it. A local test target is enough to confirm.
- **Fix:** allowlist the hosts/schemes you permit; resolve the DNS and block private, loopback, and link-local ranges; disable redirects (or re-validate each hop); never return the raw fetched body or error to the client.

## 10. CSRF (cross-site request forgery)
- **Tell:** state-changing POST/PUT/DELETE authenticated by a cookie, with no CSRF token or SameSite protection; `@csrf_exempt`, `WTF_CSRF_ENABLED=False`, CSRF disabled globally.
- **Prove:** build a tiny HTML form on another origin that POSTs to the endpoint with the victim's cookie auto-sent; if it succeeds, CSRF. (For an API with `Authorization` headers and no cookies, CSRF usually does not apply.)
- **Fix:** CSRF tokens on cookie-auth forms (most frameworks have it — keep it on), and/or `SameSite=Lax/Strict` cookies. Require a custom header for state-changing API calls.

## 11. CORS misconfiguration
- **Tell:** `Access-Control-Allow-Origin: *`, or reflecting the request's Origin back, together with `Allow-Credentials: true`; `CORS(app)` with no allowlist.
- **Prove:** `curl -H "Origin: https://evil.test" -i "$T/api/me"` and read the response headers: if `Access-Control-Allow-Origin` echoes `evil.test` and credentials are allowed, any site can read authed responses.
- **Fix:** an explicit origin allowlist; never reflect an arbitrary Origin with credentials; `*` only for truly public, cookie-free, read-only data.

## 12. Secrets & cryptography
- **Tell:** API keys, DB passwords, JWT secrets, private keys hardcoded or committed in `.env`; secrets in logs or client bundles; `verify=False`/`rejectUnauthorized:false`; home-rolled crypto; a weak/predictable token from `Math.random`/`random`.
- **Prove:** grep the repo and the git history (`git log -p | grep -i`), and check what ships to the browser; try the found key against the service to confirm it is live.
- **Fix:** secrets from the environment, out of git (`.gitignore`, `.env.example` with blanks), rotated if ever exposed; TLS verification on; standard libraries for crypto; security tokens from `secrets`/`crypto.randomBytes`.

## 13. Security misconfiguration
- **Tell:** debug mode on in production (Django `DEBUG=True`, Flask `debug=True` → Werkzeug console RCE, `app.run(debug=True)`); stack traces/SQL/paths in error responses; directory listing on; default or sample credentials; admin/actuator/`.git`/`.env` reachable; missing security headers.
- **Prove:** trigger an error (bad input, wrong type) and see if a stack trace or framework debug page comes back; `curl -i $T/.env`, `$T/.git/config`, `$T/admin`; check response headers for CSP, `X-Content-Type-Options`, `Referrer-Policy`, HSTS.
- **Fix:** debug off and a generic error page in production (log the detail server-side); block dotfiles and VCS dirs; remove defaults; add the security headers; disable unused features and verbose banners.

## 14. Open redirect
- **Tell:** `redirect(request.args['next'])` / `res.redirect(req.query.url)` with no check — often in login `?next=` or logout.
- **Prove:** `curl -i "$T/login?next=https://evil.test"` and see if `Location` sends you off-site.
- **Fix:** only redirect to relative paths or an allowlist of hosts; reject absolute URLs to other origins.

## 15. Mass assignment / over-posting
- **Tell:** `User(**request.json)` / `Model.objects.create(**body)` / `$user->fill($request->all())` — the handler trusts every field the client sends, including `is_admin`, `role`, `balance`, `verified`.
- **Prove:** on a profile-update endpoint, add `"is_admin": true` (or `role`, `price`, `user_id`) to the body and see if it sticks.
- **Fix:** bind only an explicit allowlist of fields (a schema/DTO, `fillable`, `select_fields`); never spread the raw body into a model.

## 16. Missing rate limiting / resource abuse
- **Tell:** no limit on login, signup, password reset, OTP, search, or an expensive/AI endpoint; unbounded page size; a regex on input that can blow up (ReDoS); no upload size cap.
- **Prove:** loop the endpoint fast and see nothing slows or blocks; request a huge `limit`/`per_page`; send a pathological input to a known-bad regex and watch CPU/latency.
- **Fix:** per-ip and per-account rate limits with backoff on sensitive and costly routes; a hard maximum page size; bounded, tested regexes (or a parser); request and upload size limits.

## 17. Unrestricted file upload
- **Tell:** accepting uploads by extension only, saving under the web root, keeping the client's filename, no size/type cap — a `.php`/`.jsp`/`.svg`(with script) becomes executable or stored XSS.
- **Prove:** upload a file with a disallowed type renamed to an allowed extension, or a polyglot, and see if it is stored and reachable/executed.
- **Fix:** validate by real content type (magic bytes), not extension; store outside the web root with a generated name; serve with a correct `Content-Type` and `Content-Disposition: attachment`; cap size; strip metadata; never execute the upload directory.

## 18. Vulnerable & outdated dependencies
- **Tell:** old pinned versions, lockfiles untouched for years, known-CVE packages.
- **Prove:** `pip-audit`, `npm audit`, `composer audit`, `osv-scanner`, or `trivy fs .` if available (install with `terminal_run` if the user agrees).
- **Fix:** upgrade the flagged packages to patched versions, run the tests, and keep the lockfile current. Remove dependencies you do not use.

## 19. XXE (XML external entities)
- **Tell:** an XML parser (lxml, `DocumentBuilder`, `SimpleXML`, `libxml`) fed request XML with external entities enabled (often the default in older libs).
- **Prove:** POST XML with a `<!DOCTYPE ... SYSTEM "file:///etc/passwd">` entity to your own endpoint and see if the file content comes back.
- **Fix:** disable DTDs and external-entity resolution on the parser (`resolve_entities=False`, `FEATURE_SECURE_PROCESSING`, `libxml_disable_entity_loader`). Prefer JSON.

## 20. Prompt injection & unsafe tool use (AI apps)
If the app itself calls an LLM, the model's input is another attack surface.
- **Tell:** user text concatenated into the system prompt; the model's output used to build SQL/shell/HTML or to decide an action without a check; tools the model can call with no authorization or allowlist; untrusted documents/web pages fed to the model that can carry instructions.
- **Prove:** send "ignore previous instructions and ..." / "output the system prompt" / content that tells the model to call a dangerous tool, and see if it obeys or leaks.
- **Fix:** keep untrusted text as data, never as instructions (clear separation, delimiters, and a system prompt that says tool-result text is data); never run the model's output as code/SQL/shell without the same validation you would apply to any input; gate every tool behind real authorization and an allowlist; treat fetched pages and uploaded files as hostile.

---
**What a grep can never find:** classes 1, 2, 15, 16, and most logic flaws come from *missing* checks, not dangerous functions. Read every state-changing and data-returning handler and ask: who may do this, to *whose* data, how often, and what if they send a field/id/amount they should not? That reading, plus the dynamic proofs above, is the real audit.
