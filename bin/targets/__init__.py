"""Sync targets registry.

Each sync target (bookmarks, saved tab groups, etc.) lives in its own module
and conforms to the protocol below. The CLI iterates over `ALL_TARGETS` to
push or pull, treating each one uniformly.

A target is responsible for:
  - extract(profile_dir)  → read live state from Helium's profile directory
                            (must be safe while Helium is running; may use
                            a tmpdir snapshot for files Helium has locked)
  - apply(profile_dir, data, backup_dir)
                          → write data into Helium's profile, replacing live
                            state. Caller MUST ensure Helium is not running.
                            A timestamped backup of pre-existing state is
                            written to backup_dir first.
  - serialize(data)       → render data as canonical text for state/<file>;
                            output must be deterministic
  - deserialize(text)     → inverse of serialize
  - semantically_equal(a, b)
                          → True if a and b represent the same user-visible
                            state (used for status reporting)

Targets carry two attributes:
  - name           : short identifier, e.g. "bookmarks"
  - state_filename : filename within state/, e.g. "bookmarks.json"
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .bookmarks import Bookmarks
from .saved_tab_groups import SavedTabGroups


@runtime_checkable
class Target(Protocol):
    name: str
    state_filename: str

    def extract(self, profile_dir: Path) -> dict: ...
    def apply(self, profile_dir: Path, data: dict, backup_dir: Path) -> None: ...
    def serialize(self, data: dict) -> str: ...
    def deserialize(self, text: str) -> dict: ...
    def semantically_equal(self, a: dict, b: dict) -> bool: ...


# Active targets. Order doesn't matter — push/pull iterates over all.
ALL_TARGETS: list[Target] = [
    Bookmarks(),
    SavedTabGroups(),
]
