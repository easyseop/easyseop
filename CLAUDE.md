# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

This repo started as the user's GitHub profile (`github.com/easyseop`) and now also hosts an in-development project, **`meetcute/`** — a private matchmaking admin tool. Two unrelated concerns share the tree:

1. **Profile content** (root `README.md` + `profile-3d-contrib/*.svg`) — the user's public profile.
2. **`meetcute/`** — a FastAPI app for personal use. See `meetcute/README.md` and `meetcute/MANUAL.md`.

The two should be treated independently. Don't pull profile-related files into the meetcute project or vice versa.

## Architecture

Two pieces of content, only one of which is hand-edited:

- `README.md` — the profile page. Hand-edited. Embeds `./profile-3d-contrib/profile-season-animate.svg` at the top, plus shields.io badges, a Solved.ac (Baekjoon) badge, a hits counter, and `github-readme-stats`. Note that a trailing HTML comment (`<!--`) in the current file is never closed, which hides the boilerplate footer on the rendered page.
- `profile-3d-contrib/*.svg` — auto-generated 3D contribution visualizations. **Do not edit by hand.** They are overwritten by the scheduled workflow.

### The regeneration workflow

`.github/workflows/profile-3d.yml` runs daily at 18:00 UTC (03:00 JST) and on manual `workflow_dispatch`. It:

1. Checks out the repo.
2. Runs `yoshi389111/github-profile-3d-contrib@0.6.0`, which reads the owner's contribution graph via `GITHUB_TOKEN` and writes a fresh set of SVGs into `profile-3d-contrib/`.
3. Commits everything as `generated` and pushes back to the default branch.

The flat commit log full of `generated` commits is this workflow, not human activity. When pulling/rebasing, expect frequent churn in `profile-3d-contrib/` from `origin/main`.

## Conventions

- Only `README.md` (and the workflow itself, when needed) should ever be hand-edited. If you change an SVG manually it will be clobbered on the next scheduled run.
- If `README.md` references a new SVG variant, confirm the filename exists in `profile-3d-contrib/` — the action produces a fixed set (`profile-green[-animate].svg`, `profile-season[-animate].svg`, `profile-south-season[-animate].svg`, `profile-night-view.svg`, `profile-night-green.svg`, `profile-night-rainbow.svg`, `profile-gitblock.svg`).
- To pin or upgrade the generator, change the `yoshi389111/github-profile-3d-contrib@<version>` ref in the workflow.
- To preview README changes, push to a branch and view the rendered file on GitHub; there is no local toolchain.

## meetcute (private matchmaking admin)

A Python web app under `meetcute/`. Stack: **FastAPI + SQLModel + SQLite (default) / MySQL (opt-in via env) + Jinja2 + HTMX + Tailwind (CDN)**. Solo/multi-admin matchmaking tool.

### Run
```bash
cd meetcute
pip install -e .   # or: uv sync
python -m app.seed       # optional: sample data
uvicorn app.main:app --reload
# http://127.0.0.1:8765 (or :8000 if running uvicorn directly without dev.sh)
```

### Database
- **Default**: SQLite at `meetcute/data/meetcute.db` (auto-created). No setup required.
- **Override**: set `MEETCUTE_DB_URL` to a SQLAlchemy URL — `mysql+pymysql://...` and `postgresql+psycopg2://...` both supported. `pymysql` is in deps.
- **Auto-bootstrap**: `init_db()` in `app/database.py` runs `CREATE DATABASE IF NOT EXISTS` against the server first (no-op for SQLite), then `SQLModel.metadata.create_all`.
- **Enum columns** use `SAEnum` so SQLAlchemy converts to/from the Python enum on read/write — don't change them to plain `Text`/`String`, you'll lose enum semantics and `outcome.is_active` will crash with `'str' object has no attribute`.
- **Long-text fields** (`ideal_type`, `notes`, `EncounterEvent.note`, `PersonRevision.snapshot_json`) are explicit `Column(Text)` so MySQL doesn't reject them as VARCHAR-without-length.
- Photos live in `meetcute/uploads/{person_id}/...` (filesystem, not DB).
- Both `data/` and `uploads/` are gitignored.

### Architecture
- **Models** (`app/models.py`): `User` (with `telegram_chat_id`), `Person` (with `public_id` and `owner_user_id`), `Photo`, `Encounter`, `PersonRevision`, `EncounterEvent`, `IntroductionRequest`. `Person.owner_user_id` is the admin who manages contact with that real-life person; cross-admin introductions go through `IntroductionRequest`. `Encounter` is the source of truth for match state; `PersonRevision` for profile-edit history; `EncounterEvent` for outcome transition history (one row per outcome change).
- **Derived data, never stored.** Status (`AVAILABLE / IN_PROGRESS / MATCHED`) lives in `app/services/status.py`. Per-person activity stats (counts, last_activity, days_dormant) live in `app/services/activity.py`. Both expose batched helpers (`statuses_for_persons`, `activity_for_persons`) — prefer those in list views to avoid N+1.
- **Profile edit history.** `app/services/revisions.py` snapshots tracked fields (`age`, `location`, `workplace`, `height_cm`, `ideal_type`, `notes`, `alias`) BEFORE applying an edit. `update_person` only records a revision when text fields actually change (photo-only edits don't snapshot). `diff_against` (vs current Person) and `diff_between` (vs newer revision) produce the field-level diffs the detail page renders.
- **Routers** in `app/routers/`: `auth`, `persons`, `encounters`, `compatibility`, `users`, `manual`, `requests`, `settings`. Each owns its templates under `app/templates/<feature>/`.
- **Cross-admin intro flow** (`routers/requests.py`): a sender picks a target person owned by another admin, optionally adds a message; receiver gets a Telegram notification (if configured), reviews on `/requests`, then accepts (which auto-creates an `Encounter` row in PENDING and links back via `resolved_encounter_id`) or declines. Either side can withdraw before resolution. All requests gracefully fall through if `MEETCUTE_TELEGRAM_BOT_TOKEN` or the recipient's `telegram_chat_id` isn't set — telegram failures are swallowed and don't block the workflow.
- **Notifications** (`app/notifications.py`): uses stdlib `urllib` against the Telegram Bot API. Single `send_telegram(chat_id, text)` function. No new HTTP client deps.
- **Templates** use the new Starlette signature: `templates.TemplateResponse(request, "x.html", {...})` — request goes first, NOT inside the dict.
- **Hard delete with snapshot.** `Person` deletion wipes the row + photo files + `PersonRevision` rows but loops through related `Encounter` rows, NULLs the FK, and writes `"<public_id> (deleted)"` into the `*_snapshot` field so encounter history stays readable. Revisions are deleted because they're considered _that person's_ history; Encounters survive because they belong to both parties. See `routers/persons.py:delete_person`.
- **Public IDs are never reused.** `next_public_id` walks existing IDs and returns max+1 per gender prefix.

### Auth & sessions
- **Auth is OFF by default.** Set `MEETCUTE_AUTH=on` to enable. In off mode, every protected dep returns the synthetic `LOCAL_ADMIN` user (id=0, email `(local)`), and all `/auth/*` GETs/POSTs short-circuit-redirect to `/`. The off-mode banner is rendered in `base.html` (driven by the `AUTH_ENABLED` Jinja global registered in `templating.py`).
- Starlette `SessionMiddleware` (signed cookie, 2-week max age). Secret comes from `MEETCUTE_SECRET` env; with the dev fallback it logs a warning on startup.
- `app/auth.py` exposes `require_login` and `require_admin` deps; both raise `HTTPException(303, headers={"Location": ...})` to redirect unauthenticated/unauthorized users (to `/auth/login` and `/auth/pending` respectively).
- Auth + manual routers are public. All matchmaking routers are mounted with `dependencies=[Depends(require_admin)]` in `main.py`. The `users` router applies `require_admin` itself per-endpoint so it can read `current_user`.
- **Bootstrap**: the very first registration auto-promotes to admin (`user_count(session) == 0`). Subsequent registrations land on `/auth/pending` until an admin promotes them via `/users`.
- The dashboard nav reads `request.session` directly (`user_id`, `user_email`, `is_admin`) so templates can render conditional links without a per-request context dep.
- Last-admin guards: `/users/{id}/toggle-admin` and `/users/{id}/delete` refuse to remove the only remaining admin. Don't loosen these without adding another bootstrap path.

### Conventions when changing meetcute
- **Always update `meetcute/MANUAL.md` in the same change** when you add/remove/rename user-facing features (routes, fields, status semantics, deletion behavior, etc.). The manual is rendered live at `/manual` from this single source — drift makes it lie to the user.
- The Phase roadmap lives in BOTH `meetcute/README.md` and `meetcute/MANUAL.md` §10. Keep them aligned.
- Don't store real names. The product policy is `public_id` only; `alias` is admin-only memo.
- Don't add per-person status as a stored column. Adding `Person.status` will diverge from encounter truth — extend `services/status.py` instead.
- `Encounter` rows are append-only in spirit: only delete when fixing data-entry mistakes. Result transitions go via `outcome` updates, and each outcome change writes an `EncounterEvent` row automatically (notes-only edits don't). Deleting the `Encounter` also wipes its `EncounterEvent` rows.
- The dashboard (`/`) and several routers import `OUTCOME_LABEL`/`OUTCOME_BADGE` from `routers/encounters.py`. If you add a new `EncounterOutcome` value, update both maps and the badge color.
- Tailwind is loaded from CDN with the `typography` plugin (`base.html`). Don't double-load; reuse `prose` classes in markdown-rendered pages.
- No tests exist yet. Smoke-test by hitting endpoints with `curl` after changes (the project is small enough that this catches regressions cheaply).
