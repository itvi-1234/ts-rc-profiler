"""Tests for the helpers in release-package.py. Run: python3 -m unittest scripts/test_release_package.py"""

import hashlib
import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("release-package.py")
spec = importlib.util.spec_from_file_location("release_package", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class VersionTagTests(unittest.TestCase):
    def test_only_stable_tags_are_accepted(self):
        for tag in ("v0.1.0", "v1.20.3"):
            self.assertTrue(release.TAG.fullmatch(tag), tag)
        for tag in ("0.1.0", "v1.0", "v1.0.0-rc1", "v01.0.0", "main", ""):
            self.assertFalse(release.TAG.fullmatch(tag), tag)


class FetchTests(unittest.TestCase):
    def test_a_download_must_match_the_pinned_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "node.tar.xz")
            source.write_bytes(b"pretend archive")
            good = hashlib.sha256(b"pretend archive").hexdigest()
            destination = Path(directory, "good")
            release.fetch(source.as_uri(), destination, good)
            self.assertEqual(destination.read_bytes(), b"pretend archive")
            bad = Path(directory, "bad")
            with self.assertRaisesRegex(ValueError, "differs from the pinned"):
                release.fetch(source.as_uri(), bad, "0" * 64)
            self.assertFalse(bad.exists(), "a download with the wrong checksum must not be kept")


class NodeLockTests(unittest.TestCase):
    def test_the_lock_pins_a_checksum_for_each_architecture(self):
        lock = release.json.loads(SCRIPT.parent.parent.joinpath("distribution/node.lock.json").read_text())
        self.assertEqual(sorted(lock["platforms"]), ["amd64", "arm64"])
        for goarch, platform in lock["platforms"].items():
            self.assertRegex(platform["sha256"], r"^[0-9a-f]{64}$", goarch)
            self.assertIn(f"v{lock['version']}", platform["file"])
            self.assertTrue(lock["baseUrl"].endswith(f"v{lock['version']}"))

    def test_the_bundled_node_satisfies_the_engines_range_of_the_profiler(self):
        package = release.json.loads(SCRIPT.parent.parent.joinpath("package.json").read_text())
        lock = release.json.loads(SCRIPT.parent.parent.joinpath("distribution/node.lock.json").read_text())
        minimum = tuple(int(part) for part in package["engines"]["node"].removeprefix(">=").split("."))
        self.assertGreaterEqual(tuple(int(part) for part in lock["version"].split(".")), minimum)


class ArchiveTests(unittest.TestCase):
    def test_equal_input_gives_equal_bytes_with_fixed_owner_and_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "root")
            (root / "app/dist").mkdir(parents=True)
            (root / "app/dist/main.js").write_text("main")
            (root / "LICENSE").write_text("license")
            first, second = Path(directory, "a.tar.gz"), Path(directory, "b.tar.gz")
            release.make_archive(root, first, 1700000000)
            release.make_archive(root, second, 1700000000)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                self.assertEqual([m.name for m in archive.getmembers()], ["LICENSE", "app", "app/dist", "app/dist/main.js"])
                self.assertTrue(all(m.uid == 0 and m.gid == 0 and m.mtime == 1700000000 for m in archive.getmembers()))


class FormulaTests(unittest.TestCase):
    def test_the_release_fields_are_filled_in(self):
        template = SCRIPT.parent.parent.joinpath("distribution/homebrew/rc-profiler-typescript.rb").read_text()
        rendered = release.render_formula(template, "1.2.3", "a" * 64)
        self.assertIn('version "1.2.3"', rendered)
        self.assertIn('sha256 "' + "a" * 64 + '"', rendered)
        self.assertNotIn("{{", rendered)

    def test_an_unresolved_field_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "unresolved"):
            release.render_formula("{{VERSION}} {{OTHER}}", "1.2.3", "a" * 64)


class WrapperTests(unittest.TestCase):
    def test_the_wrapper_runs_the_bundled_node_and_passes_arguments_through(self):
        self.assertTrue(release.WRAPPER.startswith("#!/bin/sh\n"))
        self.assertIn("exec /usr/lib/rc-profiler-typescript/node/bin/node /usr/lib/rc-profiler-typescript/app/dist/src/main.js \"$@\"", release.WRAPPER)


if __name__ == "__main__":
    unittest.main()
