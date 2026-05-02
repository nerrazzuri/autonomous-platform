from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dangerous_directories() -> set[Path]:
    return {
        Path("/").resolve(),
        Path("/var").resolve(),
        Path("/var/log").resolve(),
        Path("/home").resolve(),
        Path.home().resolve(),
        _repo_root().resolve(),
    }


def _validate_base_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved in _dangerous_directories():
        raise ValueError(f"Refusing dangerous retention base directory: {resolved}")
    return resolved


@dataclass(frozen=True)
class RetentionPolicy:
    base_dirs: list[Path]
    max_age_days: int | None = None
    max_total_bytes: int | None = None
    allowed_suffixes: tuple[str, ...] = (".log", ".jsonl", ".mp4", ".jpg", ".jpeg", ".png")
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.base_dirs:
            raise ValueError("base_dirs must be explicitly provided")
        if self.max_age_days is None and self.max_total_bytes is None:
            raise ValueError("max_age_days or max_total_bytes must be provided")
        if self.max_age_days is not None and self.max_age_days <= 0:
            raise ValueError("max_age_days must be positive")
        if self.max_total_bytes is not None and self.max_total_bytes <= 0:
            raise ValueError("max_total_bytes must be positive")
        normalized_base_dirs = [_validate_base_dir(Path(base_dir)) for base_dir in self.base_dirs]
        normalized_suffixes = tuple(suffix.lower() for suffix in self.allowed_suffixes)
        if not normalized_suffixes:
            raise ValueError("allowed_suffixes must not be empty")
        object.__setattr__(self, "base_dirs", normalized_base_dirs)
        object.__setattr__(self, "allowed_suffixes", normalized_suffixes)


@dataclass
class RetentionReport:
    scanned_files: int = 0
    deleted_files: int = 0
    skipped_files: int = 0
    freed_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


@dataclass(frozen=True)
class _CandidateFile:
    path: Path
    size: int
    mtime: float


def _iter_candidate_files(policy: RetentionPolicy, report: RetentionReport) -> list[_CandidateFile]:
    candidates: list[_CandidateFile] = []
    for base_dir in policy.base_dirs:
        if not base_dir.exists():
            continue
        for root, dirnames, filenames in os.walk(base_dir, topdown=True, followlinks=False):
            current_root = Path(root)
            dirnames[:] = [dirname for dirname in dirnames if not (current_root / dirname).is_symlink()]
            for filename in filenames:
                path = current_root / filename
                if path.is_symlink():
                    report.skipped_files += 1
                    continue
                report.scanned_files += 1
                if path.suffix.lower() not in policy.allowed_suffixes:
                    report.skipped_files += 1
                    continue
                try:
                    stat = path.stat()
                except OSError as exc:
                    report.errors.append(f"{path}: {exc}")
                    continue
                candidates.append(_CandidateFile(path=path, size=int(stat.st_size), mtime=float(stat.st_mtime)))
    return candidates


def _mark_deleted(report: RetentionReport, size: int) -> None:
    report.deleted_files += 1
    report.freed_bytes += size


def _delete_candidate(candidate: _CandidateFile, policy: RetentionPolicy, report: RetentionReport) -> bool:
    if policy.dry_run:
        _mark_deleted(report, candidate.size)
        return True
    try:
        candidate.path.unlink()
    except OSError as exc:
        report.errors.append(f"{candidate.path}: {exc}")
        return False
    _mark_deleted(report, candidate.size)
    return True


def apply_retention(policy: RetentionPolicy) -> RetentionReport:
    report = RetentionReport(dry_run=policy.dry_run)
    candidates = _iter_candidate_files(policy, report)

    remaining: list[_CandidateFile] = []
    if policy.max_age_days is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - (policy.max_age_days * 86400)
        for candidate in candidates:
            if candidate.mtime < cutoff:
                deleted = _delete_candidate(candidate, policy, report)
                if not deleted:
                    remaining.append(candidate)
            else:
                remaining.append(candidate)
    else:
        remaining = list(candidates)

    if policy.max_total_bytes is not None:
        existing_candidates = [candidate for candidate in remaining if candidate.path.exists()]
        total_bytes = sum(candidate.size for candidate in existing_candidates)
        if total_bytes > policy.max_total_bytes:
            for candidate in sorted(existing_candidates, key=lambda item: item.mtime):
                if total_bytes <= policy.max_total_bytes:
                    break
                deleted = _delete_candidate(candidate, policy, report)
                if deleted:
                    total_bytes -= candidate.size

    return report
