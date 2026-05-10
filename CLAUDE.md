# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

This is the special GitHub profile repository for the user `easyseop` — `README.md` is rendered on the user's GitHub profile page (`github.com/easyseop`). There is no application code, build system, or test suite here; the repo exists to serve profile content and regenerate a 3D contribution graph on a schedule.

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
