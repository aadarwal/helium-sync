# helium-sync

[![tests](https://github.com/aadarwal/helium-sync/actions/workflows/test.yml/badge.svg)](https://github.com/aadarwal/helium-sync/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/aadarwal/helium-sync)](https://github.com/aadarwal/helium-sync/releases)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Bidirectional sync of [Helium browser](https://helium.computer) bookmarks + saved tab groups across Macs, using your own private git repo as the transport. CLI, no extension.

A workaround until [imputnet/helium#90](https://github.com/imputnet/helium/issues/90) lands native sync.

```
helium-sync push     # done on this device  → git push
helium-sync pull     # starting on this one  → git pull → write to live profile
```

Two devices, one user, sequential. Last push wins.

## install

```bash
brew install leveldb protobuf
git clone https://github.com/aadarwal/helium-sync ~/.local/share/helium-sync
cd ~/.local/share/helium-sync
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
ln -sf "$PWD/bin/helium-sync" /opt/homebrew/bin/helium-sync
```

Bookmarks live in **your** private data repo, separate from this code:

```bash
mkdir ~/helium-data && cd ~/helium-data && git init
git remote add origin git@github.com:<you>/helium-data.git

mkdir -p ~/.config/helium-sync
echo "repo = \"$HOME/helium-data\"" > ~/.config/helium-sync/config.toml
```

First time on each device:

```bash
helium-sync init     # device whose bookmarks you want to keep (run once)
helium-sync adopt    # every other device (replaces local state)
```

No Go installed? Grab the prebuilt `leveldb-writer` for your arch from [releases](https://github.com/aadarwal/helium-sync/releases).

## commands

```
helium-sync push      snapshot live → git push     (Helium can stay running)
helium-sync pull      git pull → write to live     (Helium must be quit; Cmd-Q first)
helium-sync status    diff live vs canonical
helium-sync log       recent sync commits
helium-sync gc        prune logs/ backups older than 30 days
helium-sync init      first-time bootstrap on the source-of-truth device
helium-sync adopt     first-time bootstrap on a new device
```

Discipline: pull at start of session, push at end. Backups under `logs/prePull.<ts>/` if you slip.

## architecture

Two targets, two formats, one transport.

**Bookmarks** — `Default/Bookmarks` is JSON. Read it, zero `checksum`, write back atomically. That's the whole target.

**Saved tab groups** — protobuf entries keyed `saved_tab_group-dt-<UUID>` in a LevelDB at `Default/Sync Data/LevelDB/`. Read via `leveldbutil dump` over the SSTable files directly: no LevelDB lock contention, so `push` works while Helium runs. Write via a small Go binary (`bin/leveldb-writer`, ~80 lines using `syndtr/goleveldb`); `pull` requires Helium quit because LevelDB writes do need the lock.

`plyvel` won't work — Apple Silicon Homebrew's `libleveldb.dylib` is built with `-fvisibility=hidden` (zero exported leveldb symbols, `dlopen` fails). The Go binary sidesteps the C++ library entirely.

**Transport** — real git. `push` does `git add state/ && git commit && git push`. `pull` does `git pull --rebase`. State is plain JSON, so `git log` is your sync history and `git diff` is your bookmark diff. No three-way merge: for sequential single-user use, "last push wins" is simpler than reasoning about divergent histories.

## what's not synced

History, cookies, passwords, extensions, settings, themes, live open tabs. Privacy-sensitive, per-device-by-design, or wrong shape. Live tabs return per-device via Helium → Settings → On startup → *Continue where you left off*.

## scope

macOS only (arm64 + Intel). One user, two devices, sequential. No Linux. No Windows. No browser extension. No auto-merge. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

Built for personal use, MIT-licensed, shared because [imputnet/helium#90](https://github.com/imputnet/helium/issues/90) is taking a while. Issues and PRs welcome but slow.
