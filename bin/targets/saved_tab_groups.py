"""Saved tab groups sync target.

Reads from Helium's Sync Data LevelDB without quitting the browser by shelling
out to `leveldbutil dump`, which reads .ldb and .log files directly without
contending for the LevelDB lock that Helium holds while running.

Each entry under `saved_tab_group-dt-<UUID>` is a sync-engine local-model
envelope (LocalEntityWrapper, reconstructed from observation) wrapping a
SavedTabGroupSpecifics proto from upstream Chromium. LevelDB keeps multiple
versions per key (sequence-numbered); we deduplicate by keeping the highest
sequence number across all .ldb and .log files in the directory.

Phase C: extract + serialize + deserialize + semantically_equal (this file).
Phase D: apply (writeback to live LevelDB; requires Helium quit).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The generated .pb2 files use absolute imports (the way protoc emits them by
# default). Add the proto dir to sys.path so they can find each other.
_PROTO_DIR = Path(__file__).resolve().parent / "_proto"
sys.path.insert(0, str(_PROTO_DIR))
import local_entity_wrapper_pb2 as _wrapper_pb       # noqa: E402
import saved_tab_group_specifics_pb2 as _stg_pb      # noqa: E402


KEY_PREFIX = "saved_tab_group-dt-"

# `leveldbutil dump <file.ldb>` produces:
#   'key' @ seqnum : val => 'val'
_LDB_LINE = re.compile(r"^'(?P<key>.+?)' @ (?P<seq>\d+) : val => '(?P<val>.*)'$")

# `leveldbutil dump <file.log>` produces write batches:
#   --- offset N; sequence S
#     put 'key' 'val'
#     del 'key'
_LOG_HEADER = re.compile(r"^--- offset \d+; sequence (?P<seq>\d+)$")
_LOG_PUT = re.compile(r"^  put '(?P<key>.+?)' '(?P<val>.*)'$")
_LOG_DEL = re.compile(r"^  del '(?P<key>.+?)'$")


class SavedTabGroups:
    name = "saved_tab_groups"
    state_filename = "saved_tab_groups.json"

    def _leveldb_dir(self, profile_dir: Path) -> Path:
        return profile_dir / "Default" / "Sync Data" / "LevelDB"

    # ------------------------------------------------------------------ #
    # Extract — safe while Helium runs
    # ------------------------------------------------------------------ #

    def extract(self, profile_dir: Path) -> dict:
        ldb_dir = self._leveldb_dir(profile_dir)
        if not ldb_dir.exists():
            raise FileNotFoundError(f"no LevelDB at {ldb_dir}")

        # key -> (highest_seqnum, value_bytes_or_None_for_tombstone)
        latest: dict[str, tuple[int, bytes | None]] = {}

        for path in sorted(ldb_dir.glob("*.ldb")):
            for key, seq, val in _parse_ldb(_run_leveldbutil(path)):
                cur = latest.get(key)
                if cur is None or seq > cur[0]:
                    latest[key] = (seq, val)

        for path in sorted(ldb_dir.glob("*.log")):
            for key, seq, val in _parse_log(_run_leveldbutil(path)):
                cur = latest.get(key)
                if cur is None or seq > cur[0]:
                    latest[key] = (seq, val)

        groups: dict[str, dict] = {}
        tabs: dict[str, dict] = {}

        for key, (_, val) in latest.items():
            if not key.startswith(KEY_PREFIX):
                continue
            if val is None:
                continue  # tombstone

            wrapper = _wrapper_pb.LocalEntityWrapper()
            wrapper.ParseFromString(val)
            spec = _stg_pb.SavedTabGroupSpecifics()
            spec.ParseFromString(wrapper.specifics)

            base = {
                "guid": spec.guid,
                "creation_time": spec.creation_time_windows_epoch_micros,
                "update_time":   spec.update_time_windows_epoch_micros,
                "version":       spec.version,
            }
            entity = spec.WhichOneof("entity")
            if entity == "group":
                g = spec.group
                groups[spec.guid] = {
                    **base,
                    "title":    g.title,
                    "color":    int(g.color),
                    "position": g.position,
                }
            elif entity == "tab":
                t = spec.tab
                tabs[spec.guid] = {
                    **base,
                    "group_guid": t.group_guid,
                    "url":        t.url,
                    "title":      t.title,
                    "position":   t.position,
                }
            # else: neither — skip

        return {"groups": groups, "tabs": tabs}

    # ------------------------------------------------------------------ #
    # Apply — write merged state into the live LevelDB
    # ------------------------------------------------------------------ #

    def apply(self, profile_dir: Path, data: dict, backup_dir: Path) -> None:
        """Replace the saved-tab-group entries in the live LevelDB with `data`.

        Preconditions:
          - Helium MUST not be running (it holds an exclusive flock on LOCK).
          - `bin/leveldb-writer` must exist (built from bin/_go/leveldb_writer).

        Steps:
          1. Snapshot the entire LevelDB directory to backup_dir/LevelDB/
             (recoverable via cp -r if anything goes wrong).
          2. Compute the diff: which `saved_tab_group-dt-*` keys to put,
             which to delete (anything currently present that isn't in
             `data`). Other keys (web_apps-*, metadata) are never touched.
          3. Encode each group/tab as LocalEntityWrapper(specifics=
             SavedTabGroupSpecifics(...)) protobuf bytes.
          4. Invoke the Go writer with a JSON ops file.
        """
        ldb_dir = self._leveldb_dir(profile_dir)
        if not ldb_dir.exists():
            raise FileNotFoundError(f"no LevelDB at {ldb_dir}")

        # 1. Backup
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_target = backup_dir / "LevelDB"
        if backup_target.exists():
            shutil.rmtree(backup_target)
        shutil.copytree(ldb_dir, backup_target)

        # 2. Compute deletion set
        existing = self.extract(profile_dir)
        existing_keys = (
            {f"{KEY_PREFIX}{guid}" for guid in existing.get("groups", {})}
            | {f"{KEY_PREFIX}{guid}" for guid in existing.get("tabs", {})}
        )
        target_keys = (
            {f"{KEY_PREFIX}{guid}" for guid in data.get("groups", {})}
            | {f"{KEY_PREFIX}{guid}" for guid in data.get("tabs", {})}
        )

        ops: list[dict] = []
        for guid, g in data.get("groups", {}).items():
            ops.append({
                "op": "put",
                "key": f"{KEY_PREFIX}{guid}",
                "val_hex": _encode_group(g).hex(),
            })
        for guid, t in data.get("tabs", {}).items():
            ops.append({
                "op": "put",
                "key": f"{KEY_PREFIX}{guid}",
                "val_hex": _encode_tab(t).hex(),
            })
        for key in existing_keys - target_keys:
            ops.append({"op": "delete", "key": key})

        # 3. Invoke Go writer
        writer = Path(__file__).resolve().parent.parent / "leveldb-writer"
        if not writer.exists():
            raise FileNotFoundError(
                f"leveldb-writer binary not found at {writer}. "
                "Build it with: cd bin/_go/leveldb_writer && go build -o ../../leveldb-writer ."
            )

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(ops, f)
            ops_path = f.name
        try:
            r = subprocess.run(
                [str(writer), "-db", str(ldb_dir), "-ops", ops_path],
                check=False, capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(
                    f"leveldb-writer failed (exit {r.returncode}):\n"
                    f"stdout: {r.stdout}\nstderr: {r.stderr}"
                )
        finally:
            Path(ops_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Serialization — deterministic JSON
    # ------------------------------------------------------------------ #

    def serialize(self, data: dict) -> str:
        groups = {k: data["groups"][k] for k in sorted(data.get("groups", {}))}
        tabs = {k: data["tabs"][k] for k in sorted(data.get("tabs", {}))}
        return json.dumps({"groups": groups, "tabs": tabs}, indent=2, sort_keys=True)

    def deserialize(self, text: str) -> dict:
        return json.loads(text)

    # ------------------------------------------------------------------ #
    # Semantic equality — used for status output, not branching logic
    # ------------------------------------------------------------------ #

    def semantically_equal(self, a: dict, b: dict) -> bool:
        ag, bg = a.get("groups", {}), b.get("groups", {})
        at, bt = a.get("tabs", {}), b.get("tabs", {})
        if set(ag) != set(bg) or set(at) != set(bt):
            return False
        for k, ag_k in ag.items():
            bg_k = bg[k]
            if (ag_k.get("title") != bg_k.get("title") or
                    ag_k.get("color") != bg_k.get("color") or
                    ag_k.get("position") != bg_k.get("position")):
                return False
        for k, at_k in at.items():
            bt_k = bt[k]
            if (at_k.get("group_guid") != bt_k.get("group_guid") or
                    at_k.get("url") != bt_k.get("url") or
                    at_k.get("title") != bt_k.get("title") or
                    at_k.get("position") != bt_k.get("position")):
                return False
        return True


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #

def _run_leveldbutil(path: Path) -> str:
    r = subprocess.run(
        ["leveldbutil", "dump", str(path)],
        capture_output=True, text=True, check=True,
    )
    return r.stdout


def _parse_ldb(text: str):
    """Yield (key, seq, val_bytes) tuples from a .ldb dump."""
    for line in text.splitlines():
        m = _LDB_LINE.match(line)
        if m:
            yield m["key"], int(m["seq"]), _unescape(m["val"])


def _parse_log(text: str):
    """Yield (key, seq, val_bytes_or_None) from a .log dump.
    val=None marks a tombstone (delete).
    """
    seq = 0
    for line in text.splitlines():
        m = _LOG_HEADER.match(line)
        if m:
            seq = int(m["seq"])
            continue
        m = _LOG_PUT.match(line)
        if m:
            yield m["key"], seq, _unescape(m["val"])
            seq += 1
            continue
        m = _LOG_DEL.match(line)
        if m:
            yield m["key"], seq, None
            seq += 1


def _encode_group(g: dict) -> bytes:
    """Encode a group dict back into a LocalEntityWrapper(SavedTabGroupSpecifics)
    protobuf, ready to be stored as a LevelDB value."""
    spec = _stg_pb.SavedTabGroupSpecifics()
    spec.guid = g["guid"]
    spec.creation_time_windows_epoch_micros = int(g.get("creation_time", 0))
    spec.update_time_windows_epoch_micros = int(g.get("update_time", 0))
    if "version" in g:
        spec.version = int(g["version"])
    spec.group.title = g.get("title", "")
    spec.group.color = int(g.get("color", 0))
    spec.group.position = int(g.get("position", 0))

    wrapper = _wrapper_pb.LocalEntityWrapper()
    wrapper.marker = 1
    wrapper.specifics = spec.SerializeToString()
    return wrapper.SerializeToString()


def _encode_tab(t: dict) -> bytes:
    spec = _stg_pb.SavedTabGroupSpecifics()
    spec.guid = t["guid"]
    spec.creation_time_windows_epoch_micros = int(t.get("creation_time", 0))
    spec.update_time_windows_epoch_micros = int(t.get("update_time", 0))
    if "version" in t:
        spec.version = int(t["version"])
    spec.tab.group_guid = t.get("group_guid", "")
    spec.tab.url = t.get("url", "")
    spec.tab.title = t.get("title", "")
    spec.tab.position = int(t.get("position", 0))

    wrapper = _wrapper_pb.LocalEntityWrapper()
    wrapper.marker = 1
    wrapper.specifics = spec.SerializeToString()
    return wrapper.SerializeToString()


def _unescape(s: str) -> bytes:
    """Decode leveldbutil's escape output back to raw bytes.

    leveldbutil's rule (see leveldb/util/logging.cc::AppendEscapedStringTo):
    bytes 0x20–0x7E are emitted literally; all others as `\\xNN`. Notably,
    a literal backslash byte (0x5C) is emitted as a single character `\\`,
    NOT doubled. Python's `codecs.unicode_escape` would mis-interpret a
    bare backslash followed by `x` plus other chars; we parse exactly
    leveldbutil's grammar instead.
    """
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 3 < n and s[i + 1] == "x":
            hh = s[i + 2:i + 4]
            try:
                out.append(int(hh, 16))
                i += 4
                continue
            except ValueError:
                pass
        # Any other character — including a bare backslash — is one literal byte.
        out.append(ord(c) & 0xFF)
        i += 1
    return bytes(out)
