---
name: build-backend
description: Build backends with sound validation, auth, data access, and error handling.
triggers: backend, api, rest, endpoint, database, server, auth, login, sql, migration, migrasi, fastapi, express, django, flask, graphql, webhook, queue
---
# Building a backend

**Layers.** Keep handlers thin: parse and validate the request, call the logic, shape the response. Business logic does not know about HTTP, and data access lives in one place. Follow the project's existing structure before inventing one.

**Treat all input as hostile.** Validate type, length, range, and format at the boundary using a schema, and reject with a 4xx that says what is wrong. Use parameterised queries, never string-built SQL. Limit request and upload sizes. Never build file paths or shell commands from user input without checking them.

**Authentication and authorisation are separate.** Authentication asks who you are. Authorisation asks whether you may touch *this* record, and it must be checked on every request against the owner, not only the role. Fetching `/orders/42` without checking who owns order 42 is the most common serious API bug. Hash passwords with argon2 or bcrypt, never a plain hash. Rate limit login and other sensitive endpoints.

**Errors.** Use the right status: 400 invalid input, 401 not logged in, 403 not allowed, 404 not found, 409 conflict, 422 failed validation, 429 too many requests, 5xx our fault. Use one error shape everywhere. Log the detail on the server and return a safe message: no stack traces, SQL, or secrets to clients. Do not swallow exceptions.

**Data.** Change schemas with migrations only, and make them reversible and safe to run on existing data. Add indexes for what you filter and join on. Use transactions when several writes must succeed together. Avoid N+1 queries: if a loop queries the database, fetch in one query instead. Every list endpoint is paginated with a maximum page size.

**Reliability.** Set timeouts on every outbound call. Retry only idempotent operations, with backoff. Make webhooks and payments idempotent with a key, because they will be delivered twice. Move slow work to a queue instead of holding a request open.

**Secrets and config.** Read them from the environment, keep them out of git and logs, and provide an `.env.example`. Default to the safe option: CORS allowlist rather than `*`, debug mode off.

**Verify.** Run `run_checks`, then call the endpoint for real: the success case, invalid input, no auth, and someone else's record. Test the data layer against a real database when you can, because mocks hide query bugs.
