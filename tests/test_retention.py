from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_file(path: Path, content: bytes, *, age_days: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if age_days is not None:
        old_time = time.time() - (age_days * 86400)
        os.utime(path, (old_time, old_time))
    return path


def test_retention_refuses_dangerous_base_directory() -> None:
    from shared.observability.retention import RetentionPolicy

    with pytest.raises(ValueError):
        RetentionPolicy(base_dirs=[Path("/")], max_age_days=30)


def test_age_based_cleanup_deletes_only_old_allowed_files(tmp_path: Path) -> None:
    from shared.observability.retention import RetentionPolicy, apply_retention

    old_log = _write_file(tmp_path / "logs" / "old.log", b"old", age_days=40)
    old_audit = _write_file(tmp_path / "audit" / "old.jsonl", b"{}\n", age_days=45)
    new_log = _write_file(tmp_path / "logs" / "new.log", b"new", age_days=1)

    report = apply_retention(RetentionPolicy(base_dirs=[tmp_path], max_age_days=30))

    assert report.deleted_files == 2
    assert not old_log.exists()
    assert not old_audit.exists()
    assert new_log.exists()


def test_age_based_cleanup_does_not_delete_new_files(tmp_path: Path) -> None:
    from shared.observability.retention import RetentionPolicy, apply_retention

    new_file = _write_file(tmp_path / "recent.log", b"recent", age_days=2)

    report = apply_retention(RetentionPolicy(base_dirs=[tmp_path], max_age_days=30))

    assert report.deleted_files == 0
    assert new_file.exists()


def test_cleanup_ignores_disallowed_suffixes(tmp_path: Path) -> None:
    from shared.observability.retention import RetentionPolicy, apply_retention

    disallowed = _write_file(tmp_path / "keep.txt", b"keep", age_days=90)

    report = apply_retention(RetentionPolicy(base_dirs=[tmp_path], max_age_days=30))

    assert report.deleted_files == 0
    assert report.skipped_files >= 1
    assert disallowed.exists()


def test_cleanup_does_not_follow_or_delete_symlinks(tmp_path: Path) -> None:
    from shared.observability.retention import RetentionPolicy, apply_retention

    target = _write_file(tmp_path / "target.log", b"target", age_days=90)
    symlink = tmp_path / "symlink.log"
    symlink.symlink_to(target)

    report = apply_retention(RetentionPolicy(base_dirs=[tmp_path], max_age_days=30))

    assert report.deleted_files == 1
    assert report.skipped_files >= 1
    assert not target.exists()
    assert symlink.is_symlink()


def test_size_based_cleanup_deletes_oldest_files_first(tmp_path: Path) -> None:
    from shared.observability.retention import RetentionPolicy, apply_retention

    oldest = _write_file(tmp_path / "oldest.log", b"a" * 10, age_days=30)
    middle = _write_file(tmp_path / "middle.log", b"b" * 10, age_days=20)
    newest = _write_file(tmp_path / "newest.log", b"c" * 10, age_days=10)

    report = apply_retention(RetentionPolicy(base_dirs=[tmp_path], max_total_bytes=15))

    assert report.deleted_files == 2
    assert not oldest.exists()
    assert not middle.exists()
    assert newest.exists()


def test_dry_run_reports_deletions_but_preserves_files(tmp_path: Path) -> None:
    from shared.observability.retention import RetentionPolicy, apply_retention

    old_file = _write_file(tmp_path / "old.log", b"old", age_days=50)

    report = apply_retention(RetentionPolicy(base_dirs=[tmp_path], max_age_days=30, dry_run=True))

    assert report.dry_run is True
    assert report.deleted_files == 1
    assert report.freed_bytes == old_file.stat().st_size
    assert old_file.exists()


def test_deletion_errors_are_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.observability.retention import RetentionPolicy, apply_retention

    broken_file = _write_file(tmp_path / "broken.log", b"broken", age_days=50)
    original_unlink = Path.unlink

    def failing_unlink(self: Path, *args, **kwargs):
        if self == broken_file:
            raise OSError("permission denied")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    report = apply_retention(RetentionPolicy(base_dirs=[tmp_path], max_age_days=30))

    assert report.deleted_files == 0
    assert broken_file.exists()
    assert report.errors
    assert "permission denied" in report.errors[0]
