# helium-sync

[![tests](https://github.com/aadarwal/helium-sync/actions/workflows/test.yml/badge.svg)](https://github.com/aadarwal/helium-sync/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

CLI for keeping **bookmarks** and **saved tab groups** in sync between two
macOS Helium browser installs, via a private GitHub repo as the rendezvous
point. CLI-only, no browser extensions, no Google account, no cloud
sync service.

Modeled on `git push` / `git pull`. Whoever pushes most recently wins.

## What it does, what it doesn't

| | Synced | Storage |
|---|---|---|
| Bookmarks | ✓ | `Default/Bookmarks` (single JSON file) |
| Saved tab groups | ✓ | `Default/Sync Data/LevelDB/` (protobuf in a small embedded DB) |
| Live open tabs | ✗ | `Default/Sessions/` — restored per-device by Helium's "Continue where you left off" setting |
| History, cookies, passwords, extensions, settings, themes | ✗ | each stays device-local |

The unsynced things stay local on purpose: history and cookies are
privacy-sensitive, passwords are encrypted with per-device Keychain keys,
extensions/settings can legitimately differ between machines, and live
tabs change every few seconds — they're not a fit for a discrete-snapshot
sync model.

## Daily use

```bash
# Done on a device, walking away:
helium-sync push          # Helium can stay running

# Sitting down at the other device:
# (Cmd-Q in Helium first, if it's open)
helium-sync pull
# launch Helium and continue
```

Discipline: **pull at the start of a session, push at the end.** If you
forget to push before switching devices, the un-pushed device's edits
get clobbered by the next pull. They're recoverable from
`logs/prePull.<timestamp>/` but it's a manual `cp -r` to restore.

## CLI surface

```
helium-sync status                live vs canonical, per-target diff
helium-sync push                  live → repo → origin (Helium can stay running)
helium-sync pull                  origin → live (Helium MUST be quit)
helium-sync init                  one-time on the source-of-truth device
helium-sync adopt                 one-time on a new device receiving canonical
helium-sync gc                    prune backups in logs/ older than 30 days
helium-sync gc --dry-run          preview what would be pruned
helium-sync gc --keep-days N      custom retention window
helium-sync log [-n N]            recent sync commits, prettified
```

`status` example output:

```
bookmarks          ✓ 392 URLs  (in sync)
saved_tab_groups   live=6 groups, 81 tabs | canonical=4 groups, 37 tabs | +2 groups, +44 tabs
```

When live equals canonical the line collapses to `✓ N items (in sync)`.
When they differ, both counts plus the explicit delta are shown.

## First-time setup (per device)

```bash
brew install leveldb protobuf go
git clone git@github.com:aadarwal/helium-sync.git ~/code/helium-sync
cd ~/code/helium-sync
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
ln -sf "$PWD/bin/helium-sync" /opt/homebrew/bin/helium-sync   # PATH integration

# On the source-of-truth device, once:
helium-sync init

# On any other device, once:
helium-sync adopt    # destructive: replaces local state with canonical
```

`bin/leveldb-writer` is a precompiled arm64-Mac binary committed to the
repo. If you need a different arch, you have two options:

```bash
# Option A — download the prebuilt binary from the latest release:
ARCH=$(uname -m)
case "$ARCH" in
  arm64)  ASSET="leveldb-writer-darwin-arm64" ;;
  x86_64) ASSET="leveldb-writer-darwin-amd64" ;;
esac
curl -fSL -o bin/leveldb-writer \
  "https://github.com/aadarwal/helium-sync/releases/latest/download/$ASSET"
chmod +x bin/leveldb-writer

# Option B — build from source (requires Go installed):
bin/_go/build.sh
```

## Code vs. data: where does the CLI store state?

The CLI's state directory (`state/`, `logs/`) is configurable. By default
it lives in the same directory as `bin/helium-sync` (the layout above).
Override the location via, in priority order:

1. **`--repo PATH`** flag on every invocation.
2. **`HELIUM_SYNC_REPO`** environment variable.
3. **`~/.config/helium-sync/config.toml`** with `repo = "/path/to/data"`.
4. Default: parent directory of `bin/helium-sync` (backward compat).

This lets you keep the CLI installed once at a stable system location
(e.g., `~/.local/share/helium-sync/`) and your sync data in a separate
private git repo (e.g., `~/helium-data/`):

```bash
# install code (read-only, kept up to date with `git pull`):
git clone git@github.com:<owner>/helium-sync.git ~/.local/share/helium-sync
ln -sf ~/.local/share/helium-sync/bin/helium-sync /opt/homebrew/bin/helium-sync

# create your private data repo:
mkdir ~/helium-data && cd ~/helium-data
git init && git remote add origin git@github.com:<you>/helium-data.git

# point the CLI at it:
mkdir -p ~/.config/helium-sync
printf 'repo = "%s"\n' "$HOME/helium-data" > ~/.config/helium-sync/config.toml

# now this picks up ~/helium-data automatically:
helium-sync init
```

## Architecture

**Bookmarks** are a single JSON file. `extract` reads it and resets
`checksum` to `""` so it's portable across devices (Chromium recomputes
on next load). `apply` does an atomic write back. Trivial.

**Saved tab groups** are stored in a LevelDB key-value store at
`Default/Sync Data/LevelDB/`. Each key is `saved_tab_group-dt-<UUID>`,
each value is a `SavedTabGroupSpecifics` protobuf wrapped in a small
local-store envelope.

- **Read while Helium runs**: shell out to `leveldbutil dump`, which
  reads `.ldb` and `.log` files directly without acquiring the database
  lock that Helium holds. Python parses the dump and decodes the proto.
- **Write requires Helium quit**: LevelDB has an exclusive flock. We
  invoke `bin/leveldb-writer`, a tiny Go program (~80 lines) using
  `syndtr/goleveldb` (pure-Go LevelDB). Python encodes the protobuf,
  hands hex-encoded bytes to Go via a JSON ops file, Go writes the
  batch atomically.

**Why Go instead of `plyvel`**: Apple Silicon Homebrew's `libleveldb.dylib`
is built with hidden symbol visibility — `nm -gD` returns zero exported
leveldb functions. plyvel builds and links cleanly but fails at `dlopen`
because the C++ symbols it needs aren't exported. goleveldb is pure Go
and doesn't depend on the C++ library at all.

The full protocol each sync target implements is in
[`bin/targets/__init__.py`](bin/targets/__init__.py): `extract`, `apply`,
`serialize`, `deserialize`, `semantically_equal`. Adding a new sync
target is a matter of writing one more module that fits the protocol
and adding it to `ALL_TARGETS`.

## Layout

```
helium-sync/
├── bin/
│   ├── helium-sync             CLI entry (Python; auto-relaunches under .venv)
│   ├── leveldb-writer          arm64-Mac binary (committed)
│   ├── _go/                    Go source for the writer + build.sh
│   └── targets/
│       ├── __init__.py         Target protocol + ALL_TARGETS registry
│       ├── bookmarks.py
│       ├── saved_tab_groups.py
│       └── _proto/             generated Python protobuf bindings
├── proto/                      vendored Chromium .proto files + compile.sh
├── tests/                      .venv/bin/python -m unittest discover tests/
├── state/
│   ├── bookmarks.json          canonical bookmarks (committed)
│   └── saved_tab_groups.json   canonical tab groups (committed)
├── logs/                       gitignored — sync.log, prePull.<ts>/ backups
├── requirements.txt            protobuf>=4.21
└── README.md
```

## Recovery

Every `pull` writes a complete pre-modification snapshot of the live
`Bookmarks` file and `LevelDB/` directory to
`logs/prePull.<timestamp>/`. If something looks off after a pull:

```bash
ls logs/                              # find the prePull.<ts> you want
PROFILE="$HOME/Library/Application Support/net.imput.helium/Default"
cp logs/prePull.<ts>/Bookmarks "$PROFILE/Bookmarks"
cp -r logs/prePull.<ts>/LevelDB/ "$PROFILE/Sync Data/LevelDB"
# launch Helium — back to the pre-pull state
```

`helium-sync gc` prunes these after a retention window (default 30 days).

## Trade-offs (signing up for these)

- **No three-way merge.** Push overwrites the canonical wholesale; pull
  overwrites local wholesale. Discipline (pull first, push last)
  prevents data loss; backups in `logs/` enable manual recovery if the
  discipline slips.
- **Pull requires Helium quit.** Push doesn't.
- **Two-device, one-user, sequential** is the design point. Concurrent
  edits across devices in the same session result in one side's changes
  being clobbered by the other's push.
- **macOS arm64 only out of the box.** The committed `leveldb-writer`
  binary is arm64-Mac. Other arches: rebuild with `bin/_go/build.sh`.

## Tests

```bash
.venv/bin/python -m unittest discover tests/
```

47 tests including real-Helium smoke tests against the live profile
(skipped when not present). Bookmarks: extract/apply/roundtrip/semantic
equality. Saved tab groups: leveldbutil parser, protobuf decode/encode,
extract→apply round-trip on copies of the live LevelDB, modify/delete/
add/idempotency, non-saved-tab-group keys preserved during apply.
