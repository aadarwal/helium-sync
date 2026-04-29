"""Tests for CLI-level concerns. Right now: data-repo path resolution.

The CLI lives at bin/helium-sync (no .py extension), so we import it via
runpy / importlib so its module-level code (including the venv-relaunch
guard) doesn't fire. We import only the symbols we need.
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


def _import_cli():
    """Import bin/helium-sync as a module. The script has no .py extension,
    so we need an explicit SourceFileLoader (importlib's auto-detection
    only handles .py / .pyc by default). The venv-relaunch guard at the
    top of the script short-circuits because we're already running under
    .venv during tests; module import is safe.
    """
    cli_path = Path(__file__).resolve().parent.parent / "bin" / "helium-sync"
    sys.path.insert(0, str(cli_path.parent))  # so `from targets import ...` resolves
    loader = importlib.machinery.SourceFileLoader("helium_sync_cli", str(cli_path))
    spec = importlib.util.spec_from_loader("helium_sync_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestResolveRepo(unittest.TestCase):
    """_resolve_repo precedence: --repo > env > config > default."""

    def setUp(self):
        self.cli = _import_cli()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_cli_arg_wins(self):
        env_dir = self.tmp_path / "from_env"
        env_dir.mkdir()
        cli_dir = self.tmp_path / "from_cli"
        cli_dir.mkdir()
        cfg_file = self.tmp_path / "config.toml"
        cfg_file.write_text(f'repo = "{self.tmp_path}/from_cfg"\n')

        got = self.cli._resolve_repo(
            cli_arg=cli_dir,
            env={"HELIUM_SYNC_REPO": str(env_dir)},
            config_path=cfg_file,
        )
        self.assertEqual(got, cli_dir.resolve())

    def test_env_wins_over_config_and_default(self):
        env_dir = self.tmp_path / "from_env"
        env_dir.mkdir()
        cfg_file = self.tmp_path / "config.toml"
        cfg_file.write_text(f'repo = "{self.tmp_path}/from_cfg"\n')

        got = self.cli._resolve_repo(
            cli_arg=None,
            env={"HELIUM_SYNC_REPO": str(env_dir)},
            config_path=cfg_file,
        )
        self.assertEqual(got, env_dir.resolve())

    def test_config_wins_over_default(self):
        cfg_dir = self.tmp_path / "from_cfg"
        cfg_dir.mkdir()
        cfg_file = self.tmp_path / "config.toml"
        cfg_file.write_text(f'repo = "{cfg_dir}"\n')

        got = self.cli._resolve_repo(
            cli_arg=None,
            env={},
            config_path=cfg_file,
        )
        self.assertEqual(got, cfg_dir.resolve())

    def test_default_when_nothing_set(self):
        # No flag, empty env, missing config file → default fallback.
        cfg_file = self.tmp_path / "does_not_exist.toml"
        got = self.cli._resolve_repo(cli_arg=None, env={}, config_path=cfg_file)
        self.assertEqual(got, self.cli.DEFAULT_REPO.resolve())

    def test_expands_user_in_env(self):
        # ~ in env value should expand
        got = self.cli._resolve_repo(
            cli_arg=None,
            env={"HELIUM_SYNC_REPO": "~"},
            config_path=self.tmp_path / "missing.toml",
        )
        self.assertEqual(got, Path.home().resolve())

    def test_malformed_config_falls_through_to_default(self):
        # Garbage TOML → quietly fall through to default rather than crash.
        cfg_file = self.tmp_path / "config.toml"
        cfg_file.write_text("not = valid = toml")
        got = self.cli._resolve_repo(cli_arg=None, env={}, config_path=cfg_file)
        self.assertEqual(got, self.cli.DEFAULT_REPO.resolve())

    def test_empty_env_value_treated_as_unset(self):
        # HELIUM_SYNC_REPO="" should fall through, not resolve to "".
        cfg_file = self.tmp_path / "missing.toml"
        got = self.cli._resolve_repo(
            cli_arg=None,
            env={"HELIUM_SYNC_REPO": ""},
            config_path=cfg_file,
        )
        self.assertEqual(got, self.cli.DEFAULT_REPO.resolve())


if __name__ == "__main__":
    unittest.main()
