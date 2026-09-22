import hashlib
import tempfile
import unittest
from pathlib import Path

from server.result_migration import migrate_legacy_results


class ResultMigrationTest(unittest.TestCase):
    def test_repeated_migration_is_idempotent_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            legacy = base / "result"
            results = base / "workspace" / "results"
            legacy_folder = legacy / "page-1"
            legacy_folder.mkdir(parents=True)
            (legacy_folder / "final.png").write_bytes(b"image")
            mapping = results / ".legacy-migrations.json"

            migrate_legacy_results(legacy, results, mapping)
            self.assertEqual((results / "page-1" / "final.png").read_bytes(), b"image")
            self.assertEqual((legacy_folder / "final.png").read_bytes(), b"image")
            migrate_legacy_results(legacy, results, mapping)
            self.assertEqual(list(results.glob("page-1*")), [results / "page-1"])

    def test_conflicting_destination_gets_deterministic_suffix(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            legacy = base / "result"
            results = base / "workspace" / "results"
            source = legacy / "page"
            source.mkdir(parents=True)
            (source / "final.png").write_bytes(b"legacy")
            destination = results / "page"
            destination.mkdir(parents=True)
            (destination / "final.png").write_bytes(b"new")
            mapping = results / ".legacy-migrations.json"

            migrate_legacy_results(legacy, results, mapping)
            migrated = [path for path in results.glob("page-legacy-*") if path.is_dir()]
            self.assertEqual(len(migrated), 1)
            self.assertEqual((migrated[0] / "final.png").read_bytes(), b"legacy")
            self.assertEqual((destination / "final.png").read_bytes(), b"new")

    def test_existing_conflict_destination_gets_next_suffix(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            legacy = base / "result"
            results = base / "workspace" / "results"
            source = legacy / "ocrs-legacy"
            source.mkdir(parents=True)
            (source / "final.png").write_bytes(b"legacy")
            destination = results / "ocrs-legacy"
            destination.mkdir(parents=True)
            (destination / "final.png").write_bytes(b"new")
            mapping = results / ".legacy-migrations.json"

            suffix = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:10]
            existing_conflict = results / f"ocrs-legacy-legacy-{suffix}"
            existing_conflict.mkdir(parents=True)
            (existing_conflict / "final.png").write_bytes(b"previous migration")

            migrate_legacy_results(legacy, results, mapping)

            migrated = results / f"ocrs-legacy-legacy-{suffix}-1"
            self.assertEqual((migrated / "final.png").read_bytes(), b"legacy")


if __name__ == "__main__":
    unittest.main()
