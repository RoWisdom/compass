/**
 * Vitest setup — runs before any test module is imported.
 *
 * `@/env` validates `process.env` with zod and calls `process.exit(1)` when a
 * required variable is missing. Inside a vitest worker that is not a failed
 * assertion, it is a dead worker: the run reports a crashed process and the
 * real cause scrolls past. `API_BASE_URL` and `LIVE_SERVER_SECRET_KEY` have no
 * defaults, and every service test reaches them — `APIService`'s constructor
 * reads `env.API_BASE_URL`.
 *
 * Supplied here rather than left to `apps/live/.env`: that file is gitignored
 * and developer-local, so a suite that only passes on the machine that happens
 * to have it is not really a suite. `dotenv.config()` in `env.ts` does not
 * override variables that are already set, so these win.
 *
 * The values are deliberately fake — nothing in these tests makes a request.
 */
process.env.API_BASE_URL ??= "http://localhost:8000";
process.env.LIVE_SERVER_SECRET_KEY ??= "test-secret";
