from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_operator_html_exists_with_required_markers() -> None:
    operator_html = ROOT / "ui" / "operator.html"

    assert operator_html.exists()

    content = operator_html.read_text(encoding="utf-8")

    assert "Request Quadruped" in content
    assert "/ws" in content
    assert "/tasks" in content
    assert "Confirm Load" in content
    assert "Confirm Unload" in content
    assert "station_id" in content
    assert "token" in content


def test_rest_app_serves_operator_html() -> None:
    import api.rest as rest_module

    client = TestClient(rest_module.create_app())

    response = client.get("/ui/operator.html")

    assert response.status_code == 200
    assert "Request Quadruped" in response.text
