from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_floormap_js_exists_with_required_api_markers() -> None:
    floormap_js = ROOT / "ui" / "floormap.js"

    assert floormap_js.exists()

    content = floormap_js.read_text(encoding="utf-8")

    assert "window.FloorMap" in content
    assert "create" in content
    assert "updatePosition" in content
    assert "updateStatus" in content
    assert "clear" in content
    assert "destroy" in content


def test_supervisor_html_references_floormap_script() -> None:
    supervisor_html = ROOT / "ui" / "supervisor.html"

    assert supervisor_html.exists()

    content = supervisor_html.read_text(encoding="utf-8")

    assert "floormap.js" in content
