from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_supervisor_html_exists_with_required_markers() -> None:
    supervisor_html = ROOT / "ui" / "supervisor.html"

    assert supervisor_html.exists()

    content = supervisor_html.read_text(encoding="utf-8")

    assert "Supervisor Dashboard" in content
    assert "/ws" in content
    assert "/quadruped/status" in content
    assert "/queue/status" in content
    assert "/tasks" in content
    assert "/routes" in content
    assert "/estop" in content
    assert "/estop/release" in content
    assert "token" in content


def test_rest_app_serves_supervisor_html() -> None:
    import api.rest as rest_module

    client = TestClient(rest_module.create_app())

    response = client.get("/ui/supervisor.html")

    assert response.status_code == 200
    assert "Supervisor Dashboard" in response.text
