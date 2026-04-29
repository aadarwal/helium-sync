"""Tests for the bookmarks sync target.

Run from the repo root: python3 -m unittest discover tests/
"""

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from targets.bookmarks import Bookmarks  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #

def url_node(name, url, **kw):
    n = {
        "type": "url", "name": name, "url": url,
        "date_added": "100", "date_last_used": "0",
        "guid": f"g-{name}-{url}", "id": "0",
    }
    n.update(kw)
    return n


def folder_node(name, children=None, **kw):
    n = {
        "type": "folder", "name": name, "children": children or [],
        "date_added": "100", "date_modified": "100", "date_last_used": "0",
        "guid": f"g-{name}", "id": "0",
    }
    n.update(kw)
    return n


def tree(bookmark_bar=None, other=None, synced=None):
    return {
        "checksum": "abc123",
        "version": 1,
        "roots": {
            "bookmark_bar": folder_node("Bookmarks Bar", bookmark_bar or [], id="1"),
            "other":        folder_node("Other Bookmarks", other or [], id="2"),
            "synced":       folder_node("Mobile Bookmarks", synced or [], id="3"),
        },
    }


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

class TestSerialize(unittest.TestCase):
    def setUp(self):
        self.t = Bookmarks()

    def test_roundtrip(self):
        original = tree(bookmark_bar=[url_node("A", "https://a")])
        text = self.t.serialize(original)
        back = self.t.deserialize(text)
        self.assertEqual(back, original)

    def test_deterministic(self):
        data = tree(bookmark_bar=[url_node("A", "https://a"), url_node("B", "https://b")])
        self.assertEqual(self.t.serialize(data), self.t.serialize(deepcopy(data)))

    def test_serialize_emits_indented_json(self):
        text = self.t.serialize(tree(bookmark_bar=[url_node("A", "https://a")]))
        self.assertIn('"checksum"', text)
        self.assertIn("\n   ", text)  # indent=3


class TestSemanticEqual(unittest.TestCase):
    def setUp(self):
        self.t = Bookmarks()

    def test_same(self):
        a = tree(bookmark_bar=[url_node("A", "https://a")])
        self.assertTrue(self.t.semantically_equal(a, deepcopy(a)))

    def test_ignores_ids_and_dates(self):
        a = tree(bookmark_bar=[url_node("A", "https://a", id="1",   date_added="100")])
        b = tree(bookmark_bar=[url_node("A", "https://a", id="999", date_added="9999")])
        self.assertTrue(self.t.semantically_equal(a, b))

    def test_unequal_when_url_differs(self):
        a = tree(bookmark_bar=[url_node("A", "https://a")])
        b = tree(bookmark_bar=[url_node("A", "https://b")])
        self.assertFalse(self.t.semantically_equal(a, b))

    def test_unequal_when_name_differs(self):
        a = tree(bookmark_bar=[url_node("A",  "https://a")])
        b = tree(bookmark_bar=[url_node("A2", "https://a")])
        self.assertFalse(self.t.semantically_equal(a, b))

    def test_unequal_when_folder_set_differs(self):
        a = tree(bookmark_bar=[folder_node("F", [])])
        b = tree(bookmark_bar=[folder_node("G", [])])
        self.assertFalse(self.t.semantically_equal(a, b))

    def test_ignores_child_order(self):
        a = tree(bookmark_bar=[url_node("A", "https://a"), url_node("B", "https://b")])
        b = tree(bookmark_bar=[url_node("B", "https://b"), url_node("A", "https://a")])
        self.assertTrue(self.t.semantically_equal(a, b))

    def test_unequal_across_roots(self):
        # Same URL in different roots is a different bookmark.
        a = tree(bookmark_bar=[url_node("A", "https://a")])
        b = tree(other=[url_node("A", "https://a")])
        self.assertFalse(self.t.semantically_equal(a, b))


class TestExtractApply(unittest.TestCase):
    def setUp(self):
        self.t = Bookmarks()

    def _seed(self, profile: Path, data: dict) -> Path:
        (profile / "Default").mkdir(parents=True, exist_ok=True)
        live = profile / "Default" / "Bookmarks"
        live.write_text(json.dumps(data, indent=3))
        return live

    def test_extract_clears_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            self._seed(profile, tree(bookmark_bar=[url_node("A", "https://a")]))
            data = self.t.extract(profile)
            self.assertEqual(data["checksum"], "")
            self.assertIn("roots", data)

    def test_extract_preserves_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            original = tree(bookmark_bar=[
                folder_node("F", [url_node("A", "https://a")]),
                url_node("B", "https://b"),
            ])
            self._seed(profile, original)
            data = self.t.extract(profile)
            self.assertTrue(self.t.semantically_equal(data, original))

    def test_apply_writes_to_live_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            self._seed(profile, tree(bookmark_bar=[url_node("OLD", "https://old")]))
            backup_dir = profile / "logs"

            new = tree(bookmark_bar=[url_node("NEW", "https://new")])
            self.t.apply(profile, new, backup_dir)

            on_disk = json.loads((profile / "Default" / "Bookmarks").read_text())
            self.assertTrue(self.t.semantically_equal(on_disk, new))

            # Pre-existing file got backed up
            backup_file = backup_dir / "Bookmarks"
            self.assertTrue(backup_file.exists())
            backup_data = json.loads(backup_file.read_text())
            self.assertTrue(self.t.semantically_equal(
                backup_data,
                tree(bookmark_bar=[url_node("OLD", "https://old")]),
            ))

    def test_apply_no_existing_file_skips_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            backup_dir = profile / "logs"
            self.t.apply(profile, tree(bookmark_bar=[url_node("X", "https://x")]), backup_dir)
            self.assertTrue((profile / "Default" / "Bookmarks").exists())
            self.assertFalse((backup_dir / "Bookmarks").exists())

    def test_extract_apply_roundtrip(self):
        """Extract from one profile, apply to another → semantic equivalence."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_a = tmp_path / "a"
            profile_b = tmp_path / "b"
            original = tree(bookmark_bar=[
                folder_node("F", [url_node("A", "https://a"), url_node("B", "https://b")]),
                url_node("C", "https://c"),
            ])
            self._seed(profile_a, original)

            data = self.t.extract(profile_a)
            self.t.apply(profile_b, data, profile_b / "logs")

            extracted_b = self.t.extract(profile_b)
            self.assertTrue(self.t.semantically_equal(data, extracted_b))
            self.assertEqual(extracted_b["checksum"], "")


class TestRealHelium(unittest.TestCase):
    """Read-only smoke test against the user's actual Helium profile.
    Skipped if the profile isn't available. Override location via the
    HELIUM_PROFILE env var; otherwise defaults to the standard macOS path.
    """

    REAL_PROFILE = Path(os.environ.get(
        "HELIUM_PROFILE",
        str(Path.home() / "Library/Application Support/net.imput.helium"),
    ))

    def setUp(self):
        if not (self.REAL_PROFILE / "Default" / "Bookmarks").exists():
            self.skipTest("real Helium profile not available on this machine")
        self.t = Bookmarks()

    def test_extract_real_profile(self):
        data = self.t.extract(self.REAL_PROFILE)
        self.assertEqual(data["checksum"], "")
        self.assertEqual(data.get("version"), 1)
        self.assertIn("bookmark_bar", data["roots"])
        # Sanity: there's at least one URL somewhere in the tree.
        urls, _ = __import__("targets.bookmarks", fromlist=["_flatten"])._flatten(data)
        self.assertGreater(len(urls), 0)

    def test_serialize_real_data_is_deterministic(self):
        data = self.t.extract(self.REAL_PROFILE)
        self.assertEqual(self.t.serialize(data), self.t.serialize(deepcopy(data)))

    def test_extract_then_deserialize_roundtrip(self):
        data = self.t.extract(self.REAL_PROFILE)
        text = self.t.serialize(data)
        back = self.t.deserialize(text)
        self.assertTrue(self.t.semantically_equal(data, back))


if __name__ == "__main__":
    unittest.main()
