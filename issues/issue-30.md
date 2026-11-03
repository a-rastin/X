# Issue 30: Vite dev server does not reliably listen on IPv4 loopback

## Problem and cause

`make dev` starts Vite through `npm run dev -- --port "$WEB_PORT" --strictPort` ([Makefile](../Makefile)). The npm script also sets a port but neither it nor [`web/vite.config.ts`](../web/vite.config.ts) sets `server.host`. Vite 8.3.2 therefore binds `localhost`, whose resolved address depends on the machine and Node's DNS ordering. On a host where that resolves first to `::1`, Vite listens on IPv6 loopback and a connection to the distinct IPv4 address `127.0.0.1` fails. This is a bind-address issue, not a port or API proxy issue; it is host-dependent, not proof that every installation binds IPv6 only.

Evidence: this environment's Node resolves `localhost` as `::1` followed by `127.0.0.1`. Running the existing Vite dev script on an isolated port 15379 returned HTTP 200 from `[::1]` and connection refused from `127.0.0.1`. This exercised the same Vite script/configuration as `make dev`, but not its database, migration, or full startup path; clean-host acceptance has not yet been verified.

## Smallest implementation plan

1. In `web/vite.config.ts`, set `server.host` to `process.env.WEB_HOST || "127.0.0.1"`. This single Vite setting covers both `make dev` and direct `npm run dev`; an unset or empty value remains IPv4 loopback. Preserve the existing port and `/api` proxy settings and leave `preview` unchanged.
2. In [`docs/installation.md`](../docs/installation.md), alongside the local Vite port instructions, document `WEB_HOST`, its `127.0.0.1` default, and an explicit override such as `WEB_HOST=0.0.0.0 make dev` for people who need network access. Explain that the latter exposes Vite on network interfaces and should only be used deliberately on a trusted network. `make dev` sources the root `.env` after inheriting the shell, so if `.env` defines `WEB_HOST` it takes precedence over the shell value; remove or change that entry to use a shell override. Network access can also require suitable origin/HTTPS configuration; this change promises the bind address, not remote application operation.
3. With prerequisites installed, `.env` configured, ports free, and `WEB_HOST` unset in both shell and `.env`, start `make dev` on a clean host. After startup completes, verify `curl --fail http://127.0.0.1:5173/` returns the application HTML and open that URL in a browser. Confirm the listener is loopback-only. Stop the stack using the documented procedure.
4. Verify an explicit `WEB_HOST` override reaches Vite (for example, use `127.0.0.1` or `::1` where supported); if verifying `0.0.0.0`, do so only in a controlled environment and confirm the expected listener. Run the existing startup checks and `npm --prefix web run build`. No new test framework or permanent test is needed for this one-line configuration; the actual socket check is essential because the existing startup tests mock npm.

## Implementation boundaries

- Change only the Vite dev-server host setting and the local development guide, plus the project-required progress handoff/index when implementation is recorded.
- Do not change `Makefile`, package scripts, `WEB_PORT`, the backend bind address, API proxy, `PUBLIC_ORIGIN`, authentication, production deployment, Vite preview, Playwright settings, dependencies, or lockfiles. Existing startup tests assert the unchanged npm arguments; they need no edits for this configuration approach.
- Do not set the default to `0.0.0.0`, add a host value to `.env.example` that would unexpectedly mask shell overrides, or add custom DNS handling. An explicit IPv4 default may make `localhost` resolve to IPv6 on some machines; use the documented `127.0.0.1` URL for reliable access.
