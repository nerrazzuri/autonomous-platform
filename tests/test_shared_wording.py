from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_selected_shared_docstrings_use_platform_wording() -> None:
    forbidden_phrases = (
        "quadruped logistics foundation",
        "Phase 1 quadruped logistics",
        "quadruped logistics application",
    )
    files = (
        ROOT / "shared/navigation/navigator.py",
        ROOT / "shared/core/database.py",
        ROOT / "shared/core/event_bus.py",
        ROOT / "shared/core/logger.py",
    )

    for file_path in files:
        content = file_path.read_text(encoding="utf-8")
        for phrase in forbidden_phrases:
            assert phrase not in content


def test_selected_shared_files_avoid_customer_specific_terms() -> None:
    forbidden_terms = ("Sumitomo", "LINE_A", "LINE_B", "LINE_C")
    files = (
        ROOT / "shared/navigation/navigator.py",
        ROOT / "shared/core/database.py",
        ROOT / "shared/core/event_bus.py",
        ROOT / "shared/core/logger.py",
        ROOT / "shared/diagnostics/events.py",
        ROOT / "shared/audit/audit_models.py",
    )

    for file_path in files:
        content = file_path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            assert term not in content
