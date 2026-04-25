from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_refactored_project_structure_exists() -> None:
    assert (ROOT / "shared" / "core" / "config.py").exists()
    assert (ROOT / "shared" / "quadruped" / "sdk_adapter.py").exists()
    assert (ROOT / "shared" / "navigation" / "route_store.py").exists()
    assert (ROOT / "shared" / "api" / "auth.py").exists()
    assert (ROOT / "shared" / "hardware" / "video_reader.py").exists()
    assert (ROOT / "apps" / "logistics" / "tasks" / "queue.py").exists()
    assert (ROOT / "apps" / "logistics" / "ui" / "operator.html").exists()
    assert (ROOT / "apps" / "patrol" / "README.md").exists()


def test_compatibility_imports_still_work() -> None:
    old_core = importlib.import_module("core.config")
    new_core = importlib.import_module("shared.core.config")
    old_queue = importlib.import_module("tasks.queue")
    new_queue = importlib.import_module("apps.logistics.tasks.queue")

    assert old_core.get_config is new_core.get_config
    assert old_queue.TaskQueue is new_queue.TaskQueue
