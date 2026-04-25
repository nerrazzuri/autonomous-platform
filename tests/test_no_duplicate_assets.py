from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_root_ui_duplicates_are_removed() -> None:
    assert not (ROOT / "ui" / "operator.html").exists()
    assert not (ROOT / "ui" / "supervisor.html").exists()
    assert not (ROOT / "ui" / "kiosk.html").exists()
    assert not (ROOT / "ui" / "floormap.js").exists()


def test_canonical_logistics_assets_exist() -> None:
    assert (ROOT / "apps" / "logistics" / "ui" / "operator.html").exists()
    assert (ROOT / "apps" / "logistics" / "ui" / "supervisor.html").exists()
    assert (ROOT / "apps" / "logistics" / "ui" / "kiosk.html").exists()
    assert (ROOT / "apps" / "logistics" / "ui" / "floormap.js").exists()
    assert (ROOT / "apps" / "logistics" / "docs" / "phase1_runbook.md").exists()
    assert (ROOT / "apps" / "logistics" / "docs" / "deployment_checklist.md").exists()
    assert (ROOT / "apps" / "logistics" / "scripts" / "manual_e2e_smoke.sh").exists()


def test_root_logistics_docs_duplicates_are_removed() -> None:
    assert not (ROOT / "docs" / "phase1_runbook.md").exists()
    assert not (ROOT / "docs" / "deployment_checklist.md").exists()
