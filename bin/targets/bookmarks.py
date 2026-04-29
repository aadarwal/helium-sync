"""Bookmarks sync target.

Helium stores bookmarks as a single JSON file at
  Default/Bookmarks
inside its profile directory. The file is not locked; we can read it while
Helium runs (we just get whatever Chromium last flushed, modulo a ~2s
debounce). We must NOT write while Helium is running — Chromium's in-memory
bookmark service would clobber our changes on its next save.

extract() returns the live tree with checksum reset to "" so it's portable
across devices (Chromium recomputes the checksum on next load).
apply() does an atomic write (tmp + rename) to the live file.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT_KEYS = ("bookmark_bar", "other", "synced")


class Bookmarks:
    name = "bookmarks"
    state_filename = "bookmarks.json"

    # ------------------------------------------------------------------ #
    # I/O
    # ------------------------------------------------------------------ #

    def _live_path(self, profile_dir: Path) -> Path:
        return profile_dir / "Default" / "Bookmarks"

    def extract(self, profile_dir: Path) -> dict:
        live = self._live_path(profile_dir)
        data = json.loads(live.read_text())
        # Make the snapshot portable. Chromium will recompute the checksum on
        # next load; without this, the canonical bookmarks.json would carry a
        # device-specific checksum value that bounces between devices.
        data["checksum"] = ""
        return data

    def apply(self, profile_dir: Path, data: dict, backup_dir: Path) -> None:
        live = self._live_path(profile_dir)
        live.parent.mkdir(parents=True, exist_ok=True)

        if live.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, backup_dir / "Bookmarks")

        text = self.serialize(data)
        tmp = live.with_suffix(live.suffix + ".helium-sync-tmp")
        tmp.write_text(text)
        tmp.replace(live)

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def serialize(self, data: dict) -> str:
        # indent=3 matches Chromium's own bookmark-file convention; sort_keys
        # disabled because Chromium uses insertion order in the roots dict and
        # we want byte-stable output across runs (Python preserves dict order
        # since 3.7, so passing the same dict gives the same bytes).
        return json.dumps(data, indent=3, ensure_ascii=False)

    def deserialize(self, text: str) -> dict:
        return json.loads(text)

    # ------------------------------------------------------------------ #
    # Semantic equality (for status output, never branch logic)
    # ------------------------------------------------------------------ #

    def semantically_equal(self, a: dict, b: dict) -> bool:
        ua, fa = _flatten(a)
        ub, fb = _flatten(b)
        if set(ua) != set(ub):
            return False
        if fa != fb:
            return False
        for k, va in ua.items():
            vb = ub[k]
            if va.get("name") != vb.get("name"):
                return False
            if va.get("url") != vb.get("url"):
                return False
        return True


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #

def _flatten(tree: dict) -> tuple[dict, set]:
    """Walk tree → ({(parent_path, url[, dup_idx]): node}, {folder_path}).

    Identifies bookmarks by (parent folder path, URL), ignoring per-profile
    `id` and `guid`. Used only for equality comparison.
    """
    urls: dict = {}
    folders: set = set()
    if not tree or "roots" not in tree:
        return urls, folders
    for rk in ROOT_KEYS:
        node = tree["roots"].get(rk)
        if not node:
            continue
        path = (rk,)
        folders.add(path)
        _walk(node, path, urls, folders)
    return urls, folders


def _walk(node: dict, path: tuple, urls: dict, folders: set) -> None:
    for child in node.get("children") or []:
        ctype = child.get("type")
        if ctype == "url":
            url = child.get("url", "")
            key: tuple = (path, url)
            if key in urls:
                i = 2
                while (path, url, i) in urls:
                    i += 1
                key = (path, url, i)
            urls[key] = child
        elif ctype == "folder":
            cname = child.get("name", "")
            child_path = path + (cname,)
            if child_path in folders:
                i = 2
                while path + (f"{cname}#{i}",) in folders:
                    i += 1
                child_path = path + (f"{cname}#{i}",)
            folders.add(child_path)
            _walk(child, child_path, urls, folders)
