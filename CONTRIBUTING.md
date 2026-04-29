# Contributing to helium-sync

Thanks for considering it. A few notes before you open a PR.

## Maintenance posture

This project was built for personal use and shared in case it's helpful.
There's no SLA on issue triage or PR review — I get to it when I get to
it. If you need fast turnaround, fork it and run with the changes.

## Scope

What's in scope:

- Bug fixes for Helium versions on macOS arm64 and macOS x86_64
- Improvements to the Bookmarks and SavedTabGroups sync targets
- New optional sync targets that conform to the `Target` protocol in
  `bin/targets/__init__.py` (e.g., custom search engines)
- Tests, CI, docs, build tooling

What's out of scope (please don't open PRs for these):

- Linux or Windows support — the dependency stack is macOS-specific
- Bidirectional automatic merge (we deliberately use a simple
  push-overwrites-canonical model — see README "Trade-offs")
- Browser-extension form factor — this is intentionally CLI-only
- History/cookies/passwords/extensions sync — privacy-sensitive, see
  README "What it does, what it doesn't"

## Dev setup

```bash
brew install leveldb protobuf go
git clone <your fork>
cd helium-sync
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
bin/_go/build.sh   # build leveldb-writer for your arch
.venv/bin/python -m unittest discover tests/
```

## Tests

- Pure-Python tests run on every CI push to `main` and every PR (see
  `.github/workflows/test.yml`).
- Real-Helium tests (`TestRealHelium`, `TestApply`) auto-skip when no
  Helium profile is present. They run locally if you have Helium
  installed; override the path with `HELIUM_PROFILE=...` if your
  install is non-standard.

## PR conventions

- One concept per PR. Refactors and feature changes shouldn't share a PR.
- Keep the description ≤ ~10 lines. Lead with the *why*.
- All four CI checks (Linux + macOS) must be green before merge.
- Squash on merge.

## Reporting bugs

Use the issue template — Helium version, macOS arch, and the output of
the failing command are what I'll need first. Run the failing command
with `--verbose` (where supported) and paste the relevant lines.

## Architecture pointers

- `bin/helium-sync` — CLI entry point. Auto-relaunches under `.venv`.
- `bin/targets/__init__.py` — `Target` protocol. New sync targets
  conform to this and register in `ALL_TARGETS`.
- `bin/targets/bookmarks.py` — straightforward JSON file I/O.
- `bin/targets/saved_tab_groups.py` — protobuf decode/encode + LevelDB
  read via `leveldbutil dump` + LevelDB write via `bin/leveldb-writer`
  (Go binary, source in `bin/_go/`).
- `proto/` — vendored Chromium proto schemas. Regenerate the Python
  bindings with `proto/compile.sh` if upstream changes.
