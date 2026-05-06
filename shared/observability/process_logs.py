"""Generic process stdout/stderr log capture for platform helper processes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from typing import Any, IO

from shared.core.logger import get_logger
from shared.diagnostics import DiagnosticReporter, error_codes, get_diagnostic_reporter
from shared.diagnostics.redaction import REDACTION_MARKER, SENSITIVE_KEYWORDS, redact_mapping, redact_value


logger = get_logger(__name__)

DEFAULT_PROCESS_LOG_DIR = "logs"
_UNKNOWN_PROCESS = "unknown"
_PROCESS_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")


class ProcessLogCaptureError(Exception):
    """Base exception for process log capture errors."""


class ProcessStartError(ProcessLogCaptureError):
    """Raised when a managed process cannot be started."""


@dataclass
class ProcessStatus:
    """Small JSON-safe status snapshot for a captured process."""

    name: str
    command: list[str]
    pid: int | None
    start_ts: str | None
    exit_ts: str | None
    exit_code: int | None
    stdout_path: Path
    stderr_path: Path
    combined_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "pid": self.pid,
            "start_ts": self.start_ts,
            "exit_ts": self.exit_ts,
            "exit_code": self.exit_code,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "combined_path": str(self.combined_path) if self.combined_path is not None else None,
        }


def sanitize_process_name(name: str | None) -> str:
    """Return a filesystem-safe process log stem."""

    if name is None:
        return _UNKNOWN_PROCESS
    normalized = str(name).strip()
    if not normalized:
        return _UNKNOWN_PROCESS
    normalized = normalized.replace("\\", "_").replace("/", "_")
    normalized = _PROCESS_NAME_PATTERN.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._-")
    if not normalized or normalized in {".", ".."}:
        return _UNKNOWN_PROCESS
    return normalized


def redact_command(command: Sequence[object]) -> list[Any]:
    """Return a JSON-safe command list with token-like values redacted."""

    redacted: list[Any] = []
    redact_next = False
    for raw_arg in command:
        arg = str(raw_arg)
        lowered = arg.lower()
        if redact_next:
            redacted.append(REDACTION_MARKER)
            redact_next = False
            continue
        if lowered.strip().startswith("bearer "):
            redacted.append(REDACTION_MARKER)
            continue
        if any(keyword in lowered for keyword in SENSITIVE_KEYWORDS):
            if "=" in arg:
                key, _value = arg.split("=", 1)
                redacted.append(f"{key}={REDACTION_MARKER}")
            else:
                redacted.append(arg)
                redact_next = True
            continue
        redacted.append(redact_value(arg))
    return redacted


class ProcessLogCapture:
    """Run one process while redirecting stdout/stderr to deterministic log files."""

    def __init__(
        self,
        *,
        name: str,
        command: Sequence[str],
        log_dir: str | Path = DEFAULT_PROCESS_LOG_DIR,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        reporter: DiagnosticReporter | None = None,
    ) -> None:
        if not command:
            raise ValueError("command must not be empty")
        self.name = sanitize_process_name(name)
        self.command = [str(item) for item in command]
        self.log_dir = Path(log_dir)
        self.cwd = Path(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self._diagnostic_reporter = reporter
        self._process: subprocess.Popen[str] | None = None
        self._stdout_handle: IO[str] | None = None
        self._stderr_handle: IO[str] | None = None
        self.status = ProcessStatus(
            name=self.name,
            command=redact_command(self.command),
            pid=None,
            start_ts=None,
            exit_ts=None,
            exit_code=None,
            stdout_path=self._ros_log_dir / f"{self.name}.stdout.log",
            stderr_path=self._ros_log_dir / f"{self.name}.stderr.log",
            combined_path=None,
        )

    @property
    def _ros_log_dir(self) -> Path:
        return self.log_dir / "ros"

    def start(self) -> ProcessStatus:
        if self._process is not None and self._process.poll() is None:
            raise ProcessLogCaptureError("process is already running")

        self._ros_log_dir.mkdir(parents=True, exist_ok=True)
        self.status.start_ts = _utc_now_iso()
        self.status.exit_ts = None
        self.status.exit_code = None
        try:
            self._stdout_handle = self.status.stdout_path.open("w", encoding="utf-8")
            self._stderr_handle = self.status.stderr_path.open("w", encoding="utf-8")
            self._process = subprocess.Popen(
                self.command,
                cwd=str(self.cwd) if self.cwd is not None else None,
                env=self.env,
                stdout=self._stdout_handle,
                stderr=self._stderr_handle,
                text=True,
            )
            self.status.pid = self._process.pid
        except Exception as exc:
            self._close_file_handles()
            self._report_diagnostic(
                "error",
                event="process.start_failed",
                message="Process failed to start.",
                error_code=error_codes.PROCESS_START_FAILED,
                details={"error_type": type(exc).__name__, **self._status_details()},
            )
            raise ProcessStartError(f"failed to start process '{self.name}': {exc}") from exc

        self._report_diagnostic(
            "info",
            event="process.started",
            message="Process started.",
            details=self._status_details(),
        )
        return self.status

    def poll(self) -> int | None:
        if self._process is None:
            return self.status.exit_code
        exit_code = self._process.poll()
        if exit_code is not None:
            self._finalize(exit_code)
        return exit_code

    def wait(self, timeout: float | None = None) -> ProcessStatus:
        if self._process is None:
            return self.status
        exit_code = self._process.wait(timeout=timeout)
        self._finalize(exit_code)
        return self.status

    def terminate(self, timeout: float = 10.0) -> ProcessStatus:
        if self._process is None:
            self._close_file_handles()
            return self.status
        if self._process.poll() is None:
            self._process.terminate()
            try:
                exit_code = self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return self.kill()
        else:
            exit_code = self._process.returncode
        self._finalize(exit_code)
        return self.status

    def kill(self) -> ProcessStatus:
        if self._process is None:
            self._close_file_handles()
            return self.status
        if self._process.poll() is None:
            self._process.kill()
        exit_code = self._process.wait()
        self._finalize(exit_code)
        return self.status

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self.terminate()
        self._close_file_handles()

    def __enter__(self) -> "ProcessLogCapture":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _finalize(self, exit_code: int | None) -> None:
        if self.status.exit_code is not None:
            return
        self.status.exit_code = exit_code
        self.status.exit_ts = _utc_now_iso()
        self._close_file_handles()
        details = self._status_details()
        if exit_code == 0:
            self._report_diagnostic(
                "info",
                event="process.exited",
                message="Process exited.",
                details=details,
            )
        else:
            self._report_diagnostic(
                "error",
                event="process.failed",
                message="Process exited with a non-zero code.",
                error_code=error_codes.PROCESS_EXITED_NONZERO,
                details=details,
            )

    def _close_file_handles(self) -> None:
        for handle_name in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    self._report_diagnostic(
                        "warning",
                        event="process.log_capture_failed",
                        message="Process log file handle cleanup failed.",
                        error_code=error_codes.PROCESS_LOG_CAPTURE_FAILED,
                        details={"process_name": self.name, "handle": handle_name},
                    )
                finally:
                    setattr(self, handle_name, None)

    def _status_details(self) -> dict[str, Any]:
        details = {
            "process_name": self.name,
            "command": redact_command(self.command),
            "pid": self.status.pid,
            "exit_code": self.status.exit_code,
            "stdout_path": str(self.status.stdout_path),
            "stderr_path": str(self.status.stderr_path),
            "combined_path": None,
        }
        if self.cwd is not None:
            details["cwd"] = str(self.cwd)
        return redact_mapping(details)

    def _report_diagnostic(
        self,
        severity: str,
        *,
        event: str,
        message: str,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            reporter = self._diagnostic_reporter or get_diagnostic_reporter("process_logs")
            reporter.report(
                severity=severity,
                event=event,
                message=message,
                error_code=error_code,
                subsystem="process",
                source=__name__,
                details=details,
            )
        except Exception:
            logger.debug("Process diagnostic reporting failed", exc_info=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DEFAULT_PROCESS_LOG_DIR",
    "ProcessLogCapture",
    "ProcessLogCaptureError",
    "ProcessStartError",
    "ProcessStatus",
    "redact_command",
    "sanitize_process_name",
]
