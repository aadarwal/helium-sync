"""Tests for the saved tab groups sync target.

Run from the repo root: python3 -m unittest discover tests/
"""

import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from targets.saved_tab_groups import (  # noqa: E402
    KEY_PREFIX,
    SavedTabGroups,
    _parse_ldb,
    _parse_log,
    _unescape,
)


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #

def group(guid, title, color=1, position=0, **kw):
    g = {
        "guid": guid, "title": title, "color": color, "position": position,
        "creation_time": 100, "update_time": 100, "version": 1,
    }
    g.update(kw)
    return g


def tab(guid, group_guid, url, title, position=0, **kw):
    t = {
        "guid": guid, "group_guid": group_guid, "url": url, "title": title,
        "position": position, "creation_time": 100, "update_time": 100, "version": 1,
    }
    t.update(kw)
    return t


def state(groups=None, tabs=None):
    return {
        "groups": {g["guid"]: g for g in (groups or [])},
        "tabs":   {t["guid"]: t for t in (tabs   or [])},
    }


# --------------------------------------------------------------------------- #
# Unescape — leveldbutil's escape format
# --------------------------------------------------------------------------- #

class TestUnescape(unittest.TestCase):
    def test_plain_ascii(self):
        self.assertEqual(_unescape("hello"), b"hello")

    def test_hex_escape(self):
        self.assertEqual(_unescape("\\x0a"), b"\x0a")

    def test_mixed(self):
        self.assertEqual(_unescape("a\\x00b"), b"a\x00b")

    def test_literal_backslash_followed_by_hex_escape(self):
        # leveldbutil emits a literal backslash byte (0x5C) as bare `\`,
        # then the next byte 0x0A as `\x0a`. The 5-char input is 2 bytes out.
        self.assertEqual(_unescape("\\\\x0a"), b"\\\x0a")

    def test_literal_backslash_followed_by_literal_x(self):
        # Bare backslash followed by `x` and non-hex chars stays literal.
        self.assertEqual(_unescape("\\xy"), b"\\xy")

    def test_utf8_multibyte(self):
        # UTF-8 `·` (U+00B7) is bytes 0xC2 0xB7. leveldbutil emits both as
        # `\xc2\xb7` because they're outside printable ASCII range.
        self.assertEqual(_unescape("\\xc2\\xb7"), b"\xc2\xb7")

    def test_high_bit_in_string(self):
        # Field-7 varint=1 emits as `8\x01` (the `8` is `\x38` which is
        # printable; the next byte is non-printable).
        self.assertEqual(_unescape("8\\x01"), b"8\x01")


# --------------------------------------------------------------------------- #
# Dump-line parsers
# --------------------------------------------------------------------------- #

class TestParsers(unittest.TestCase):
    def test_parse_ldb_one_line(self):
        text = "'mykey' @ 42 : val => '\\x08\\x01'\n"
        out = list(_parse_ldb(text))
        self.assertEqual(out, [("mykey", 42, b"\x08\x01")])

    def test_parse_ldb_skips_unrelated(self):
        text = (
            "Some banner\n"
            "'mykey' @ 7 : val => 'abc'\n"
            "blank line\n"
        )
        self.assertEqual(list(_parse_ldb(text)), [("mykey", 7, b"abc")])

    def test_parse_log_increments_seq(self):
        text = (
            "--- offset 0; sequence 100\n"
            "  put 'a' '\\x01'\n"
            "  put 'b' '\\x02'\n"
            "  del 'c'\n"
        )
        out = list(_parse_log(text))
        self.assertEqual(out, [
            ("a", 100, b"\x01"),
            ("b", 101, b"\x02"),
            ("c", 102, None),
        ])

    def test_parse_log_multiple_batches(self):
        text = (
            "--- offset 0; sequence 100\n"
            "  put 'a' 'x'\n"
            "--- offset 50; sequence 200\n"
            "  put 'b' 'y'\n"
        )
        out = list(_parse_log(text))
        self.assertEqual(out, [("a", 100, b"x"), ("b", 200, b"y")])


# --------------------------------------------------------------------------- #
# Serialize / deserialize / semantic equality
# --------------------------------------------------------------------------- #

class TestSerialize(unittest.TestCase):
    def setUp(self):
        self.t = SavedTabGroups()

    def test_roundtrip(self):
        s = state(
            groups=[group("g1", "github", color=5, position=2)],
            tabs=[tab("t1", "g1", "https://github.com", "GitHub", position=0)],
        )
        text = self.t.serialize(s)
        back = self.t.deserialize(text)
        self.assertEqual(back, s)

    def test_deterministic(self):
        s1 = state(
            groups=[group("b", "bbb"), group("a", "aaa")],
            tabs=[tab("y", "a", "u2", "tt2"), tab("x", "a", "u1", "tt1")],
        )
        # Build s2 with different insertion order; serialize must produce the same bytes.
        s2 = state(
            groups=[group("a", "aaa"), group("b", "bbb")],
            tabs=[tab("x", "a", "u1", "tt1"), tab("y", "a", "u2", "tt2")],
        )
        self.assertEqual(self.t.serialize(s1), self.t.serialize(s2))


class TestSemanticEqual(unittest.TestCase):
    def setUp(self):
        self.t = SavedTabGroups()

    def test_same(self):
        s = state(
            groups=[group("g1", "name")],
            tabs=[tab("t1", "g1", "u", "t")],
        )
        self.assertTrue(self.t.semantically_equal(s, deepcopy(s)))

    def test_ignores_creation_and_update_times(self):
        a = state(groups=[group("g1", "n", creation_time=100, update_time=100)])
        b = state(groups=[group("g1", "n", creation_time=999, update_time=999)])
        self.assertTrue(self.t.semantically_equal(a, b))

    def test_unequal_when_group_title_differs(self):
        a = state(groups=[group("g1", "name1")])
        b = state(groups=[group("g1", "name2")])
        self.assertFalse(self.t.semantically_equal(a, b))

    def test_unequal_when_tab_url_differs(self):
        a = state(tabs=[tab("t1", "g1", "u1", "title")])
        b = state(tabs=[tab("t1", "g1", "u2", "title")])
        self.assertFalse(self.t.semantically_equal(a, b))

    def test_unequal_on_extra_group(self):
        a = state(groups=[group("g1", "n")])
        b = state(groups=[group("g1", "n"), group("g2", "n2")])
        self.assertFalse(self.t.semantically_equal(a, b))

    def test_unequal_on_position_change(self):
        a = state(tabs=[tab("t1", "g1", "u", "t", position=0)])
        b = state(tabs=[tab("t1", "g1", "u", "t", position=1)])
        self.assertFalse(self.t.semantically_equal(a, b))


# --------------------------------------------------------------------------- #
# Real-data smoke test
# --------------------------------------------------------------------------- #

class TestRealHelium(unittest.TestCase):
    REAL_PROFILE = Path(os.environ.get("HELIUM_PROFILE", str(Path.home() / "Library/Application Support/net.imput.helium")))

    def setUp(self):
        if not (self.REAL_PROFILE / "Default" / "Sync Data" / "LevelDB").exists():
            self.skipTest("real Helium LevelDB not available on this machine")
        self.t = SavedTabGroups()

    def test_extract_real_profile(self):
        data = self.t.extract(self.REAL_PROFILE)
        self.assertGreater(len(data["groups"]), 0)
        # Every tab references a real group GUID
        group_guids = set(data["groups"])
        for tid, t in data["tabs"].items():
            self.assertIn(t["group_guid"], group_guids,
                          f"tab {tid} references unknown group {t['group_guid']}")
        # Every group has a non-empty title
        for g in data["groups"].values():
            self.assertNotEqual(g["title"], "")
        # URLs look like URLs
        for t in data["tabs"].values():
            self.assertTrue(
                t["url"].startswith(("http://", "https://", "chrome://", "file://")),
                f"unexpected url scheme: {t['url'][:30]}",
            )

    def test_serialize_real_data_is_deterministic(self):
        data = self.t.extract(self.REAL_PROFILE)
        self.assertEqual(self.t.serialize(data), self.t.serialize(deepcopy(data)))

    def test_extract_serialize_deserialize_equivalent(self):
        data = self.t.extract(self.REAL_PROFILE)
        text = self.t.serialize(data)
        back = self.t.deserialize(text)
        self.assertTrue(self.t.semantically_equal(data, back))


class TestApply(unittest.TestCase):
    """Apply tests need a real LevelDB seeded from the user's live profile.
    Each test copies the live LevelDB to a tmpdir and operates there.
    The live profile is never modified.
    """

    REAL_PROFILE = Path(os.environ.get("HELIUM_PROFILE", str(Path.home() / "Library/Application Support/net.imput.helium")))

    def setUp(self):
        if not (self.REAL_PROFILE / "Default" / "Sync Data" / "LevelDB").exists():
            self.skipTest("real Helium LevelDB not available")
        writer = Path(__file__).resolve().parent.parent / "bin" / "leveldb-writer"
        if not writer.exists():
            self.skipTest("leveldb-writer not built (run go build in bin/_go/leveldb_writer)")
        self.t = SavedTabGroups()
        self.original = self.t.extract(self.REAL_PROFILE)

    def _make_test_profile(self):
        import shutil, tempfile
        tmp = Path(tempfile.mkdtemp(prefix="helium-sync-test."))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ldb_src = self.REAL_PROFILE / "Default" / "Sync Data" / "LevelDB"
        ldb_dst = tmp / "Default" / "Sync Data" / "LevelDB"
        ldb_dst.parent.mkdir(parents=True)
        shutil.copytree(ldb_src, ldb_dst)
        return tmp

    def test_roundtrip_identity(self):
        tmp = self._make_test_profile()
        self.t.apply(tmp, self.original, tmp / "logs")
        got = self.t.extract(tmp)
        self.assertTrue(self.t.semantically_equal(self.original, got))

    def test_modify_one_tab_title(self):
        tmp = self._make_test_profile()
        modified = deepcopy(self.original)
        target_guid = next(iter(modified["tabs"]))
        modified["tabs"][target_guid]["title"] = "MODIFIED"

        self.t.apply(tmp, modified, tmp / "logs")
        got = self.t.extract(tmp)

        self.assertEqual(got["tabs"][target_guid]["title"], "MODIFIED")
        # Other tabs unchanged
        for guid, t in self.original["tabs"].items():
            if guid != target_guid:
                self.assertEqual(got["tabs"][guid]["title"], t["title"])

    def test_delete_one_tab(self):
        tmp = self._make_test_profile()
        modified = deepcopy(self.original)
        deleted_guid = list(modified["tabs"])[0]
        del modified["tabs"][deleted_guid]

        self.t.apply(tmp, modified, tmp / "logs")
        got = self.t.extract(tmp)

        self.assertNotIn(deleted_guid, got["tabs"])
        self.assertEqual(len(got["tabs"]), len(self.original["tabs"]) - 1)
        self.assertEqual(len(got["groups"]), len(self.original["groups"]))

    def test_add_new_tab(self):
        tmp = self._make_test_profile()
        modified = deepcopy(self.original)
        target_group = next(iter(modified["groups"]))
        new_guid = "deadbeef-cafe-0000-1111-feedfacefeed"
        modified["tabs"][new_guid] = {
            "guid": new_guid,
            "group_guid": target_group,
            "url": "https://example.com/new",
            "title": "ADDED",
            "position": 99,
            "creation_time": 13400000000000000,
            "update_time": 13400000000000000,
            "version": 1,
        }
        self.t.apply(tmp, modified, tmp / "logs")
        got = self.t.extract(tmp)

        self.assertIn(new_guid, got["tabs"])
        self.assertEqual(got["tabs"][new_guid]["url"], "https://example.com/new")
        self.assertEqual(got["tabs"][new_guid]["group_guid"], target_group)

    def test_idempotency(self):
        tmp = self._make_test_profile()
        self.t.apply(tmp, self.original, tmp / "logs1")
        after_first = self.t.extract(tmp)
        self.t.apply(tmp, self.original, tmp / "logs2")
        after_second = self.t.extract(tmp)
        self.assertTrue(self.t.semantically_equal(after_first, after_second))

    def test_apply_creates_backup(self):
        tmp = self._make_test_profile()
        backup_dir = tmp / "logs"
        self.t.apply(tmp, self.original, backup_dir)
        # Backup directory should now contain the pre-modification LevelDB
        self.assertTrue((backup_dir / "LevelDB").exists())
        self.assertTrue(any((backup_dir / "LevelDB").iterdir()))

    def test_other_keys_untouched(self):
        """web_apps-* and metadata keys must survive an apply."""
        from targets.saved_tab_groups import _parse_ldb, _parse_log, _run_leveldbutil

        ldb_dir = lambda profile: profile / "Default" / "Sync Data" / "LevelDB"

        def all_keys(profile):
            """Properly-parsed set of keys (ignores value blobs)."""
            keys = set()
            d = ldb_dir(profile)
            for f in d.glob("*.ldb"):
                for k, _, _ in _parse_ldb(_run_leveldbutil(f)):
                    keys.add(k)
            for f in d.glob("*.log"):
                for k, _, v in _parse_log(_run_leveldbutil(f)):
                    if v is not None:  # ignore tombstones
                        keys.add(k)
            return keys

        def non_stg_keys(profile):
            return {k for k in all_keys(profile) if not k.startswith(KEY_PREFIX)}

        tmp = self._make_test_profile()
        before = non_stg_keys(tmp)
        self.t.apply(tmp, self.original, tmp / "logs")
        after = non_stg_keys(tmp)
        # Every non-saved_tab_group key that was there before must still be there.
        missing = before - after
        self.assertFalse(missing, f"lost non-saved_tab_group keys: {missing}")


if __name__ == "__main__":
    unittest.main()
