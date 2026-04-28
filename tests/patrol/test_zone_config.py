from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def write_zone_files(tmp_path: Path, zones_text: str, patrol_routes_text: str) -> tuple[Path, Path]:
    zones_file = tmp_path / "zones.yaml"
    patrol_routes_file = tmp_path / "patrol_routes.json"
    zones_file.write_text(zones_text, encoding="utf-8")
    patrol_routes_file.write_text(patrol_routes_text, encoding="utf-8")
    return zones_file, patrol_routes_file


def test_time_rule_matches_normal_window() -> None:
    from apps.patrol.observation.zone_config import TimeRule

    rule = TimeRule(after="08:00", before="17:00")

    assert rule.matches(datetime(2026, 4, 26, 8, 0, tzinfo=timezone.utc)) is True
    assert rule.matches(datetime(2026, 4, 26, 12, 30, tzinfo=timezone.utc)) is True
    assert rule.matches(datetime(2026, 4, 26, 17, 1, tzinfo=timezone.utc)) is False


def test_time_rule_matches_midnight_crossover() -> None:
    from apps.patrol.observation.zone_config import TimeRule

    rule = TimeRule(after="18:00", before="06:00")

    assert rule.matches(datetime(2026, 4, 26, 21, 15, tzinfo=timezone.utc)) is True
    assert rule.matches(datetime(2026, 4, 27, 5, 45, tzinfo=timezone.utc)) is True
    assert rule.matches(datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)) is False


def test_zone_definition_from_dict_and_to_prompt_fragment() -> None:
    from apps.patrol.observation.zone_config import ZoneDefinition

    zone = ZoneDefinition.from_dict(
        "WAREHOUSE_PERIMETER",
        {
            "description": "Warehouse perimeter and loading bay",
            "normal_objects": ["pallets", "security guards"],
            "suspicious_objects": ["unknown person"],
            "threat_objects": ["fire", "forced entry"],
            "time_rules": [
                {
                    "after": "20:00",
                    "before": "06:00",
                    "escalate_suspicious_to": "THREAT",
                }
            ],
        },
    )

    prompt_fragment = zone.to_prompt_fragment()

    assert zone.to_dict()["zone_id"] == "WAREHOUSE_PERIMETER"
    assert "Zone ID: WAREHOUSE_PERIMETER" in prompt_fragment
    assert "Description: Warehouse perimeter and loading bay" in prompt_fragment
    assert "Normal Objects: pallets, security guards" in prompt_fragment
    assert "Suspicious Objects: unknown person" in prompt_fragment
    assert "Threat Objects: fire, forced entry" in prompt_fragment
    assert "20:00" in prompt_fragment
    assert "06:00" in prompt_fragment


@pytest.mark.asyncio
async def test_load_valid_zones(tmp_path: Path) -> None:
    from apps.patrol.observation.zone_config import ZoneConfig

    zones_file, patrol_routes_file = write_zone_files(
        tmp_path,
        """
zones:
  PLANTATION_NORTH:
    description: North plantation
    normal_objects: [palm trees]
    suspicious_objects: [unknown vehicle]
    threat_objects: [fire]
        """.strip(),
        """
{
  "routes": [
    {
      "id": "PATROL_1",
      "active": true,
      "waypoints": [
        {
          "name": "obs-1",
          "metadata": {
            "observe": true,
            "zone_id": "PLANTATION_NORTH"
          }
        }
      ]
    }
  ]
}
        """.strip(),
    )

    config = ZoneConfig(zones_file=zones_file, patrol_routes_file=patrol_routes_file)

    await config.load()

    zone = await config.require_zone("PLANTATION_NORTH")
    zones = await config.list_zones()

    assert zone.zone_id == "PLANTATION_NORTH"
    assert config.zone_count() == 1
    assert [item.zone_id for item in zones] == ["PLANTATION_NORTH"]


@pytest.mark.asyncio
async def test_load_missing_file_raises(tmp_path: Path) -> None:
    from apps.patrol.observation.zone_config import ZoneConfig, ZoneConfigError

    config = ZoneConfig(
        zones_file=tmp_path / "missing-zones.yaml",
        patrol_routes_file=tmp_path / "patrol_routes.json",
    )

    with pytest.raises(ZoneConfigError, match="missing"):
        await config.load()


@pytest.mark.asyncio
async def test_load_malformed_yaml_raises(tmp_path: Path) -> None:
    from apps.patrol.observation.zone_config import ZoneConfig, ZoneConfigError

    zones_file, patrol_routes_file = write_zone_files(
        tmp_path,
        "zones: [broken",
        '{"routes": []}',
    )
    config = ZoneConfig(zones_file=zones_file, patrol_routes_file=patrol_routes_file)

    with pytest.raises(ZoneConfigError, match="Malformed YAML"):
        await config.load()


@pytest.mark.asyncio
async def test_missing_zone_reference_in_patrol_routes_raises(tmp_path: Path) -> None:
    from apps.patrol.observation.zone_config import ZoneConfig, ZoneConfigError

    zones_file, patrol_routes_file = write_zone_files(
        tmp_path,
        """
zones:
  WAREHOUSE_PERIMETER:
    description: Warehouse
    normal_objects: [guards]
    suspicious_objects: [unknown person]
    threat_objects: [fire]
        """.strip(),
        """
{
  "routes": [
    {
      "id": "PATROL_1",
      "active": true,
      "waypoints": [
        {
          "name": "obs-1",
          "metadata": {
            "observe": true,
            "zone_id": "PLANTATION_NORTH"
          }
        }
      ]
    }
  ]
}
        """.strip(),
    )
    config = ZoneConfig(zones_file=zones_file, patrol_routes_file=patrol_routes_file)

    with pytest.raises(ZoneConfigError, match="PLANTATION_NORTH"):
        await config.load()


@pytest.mark.asyncio
async def test_reload_if_changed(tmp_path: Path) -> None:
    from apps.patrol.observation.zone_config import ZoneConfig

    zones_file, patrol_routes_file = write_zone_files(
        tmp_path,
        """
zones:
  PLANTATION_NORTH:
    description: North plantation
    normal_objects: [palm trees]
    suspicious_objects: [unknown vehicle]
    threat_objects: [fire]
        """.strip(),
        '{"routes": []}',
    )

    config = ZoneConfig(zones_file=zones_file, patrol_routes_file=patrol_routes_file)
    await config.load()

    assert await config.reload_if_changed() is False

    time.sleep(1.1)
    zones_file.write_text(
        """
zones:
  PLANTATION_NORTH:
    description: Updated plantation
    normal_objects: [palm trees]
    suspicious_objects: [unknown vehicle]
    threat_objects: [fire]
        """.strip(),
        encoding="utf-8",
    )

    assert await config.reload_if_changed() is True
    assert (await config.require_zone("PLANTATION_NORTH")).description == "Updated plantation"


@pytest.mark.asyncio
async def test_require_zone_not_found_raises(tmp_path: Path) -> None:
    from apps.patrol.observation.zone_config import ZoneConfig, ZoneNotFoundError

    zones_file, patrol_routes_file = write_zone_files(
        tmp_path,
        """
zones:
  WAREHOUSE_PERIMETER:
    description: Warehouse
    normal_objects: [guards]
    suspicious_objects: [unknown person]
    threat_objects: [fire]
        """.strip(),
        '{"routes": []}',
    )

    config = ZoneConfig(zones_file=zones_file, patrol_routes_file=patrol_routes_file)
    await config.load()

    with pytest.raises(ZoneNotFoundError, match="PLANTATION_NORTH"):
        await config.require_zone("PLANTATION_NORTH")


def test_global_get_zone_config_returns_zone_config() -> None:
    from apps.patrol.observation.zone_config import ZoneConfig, get_zone_config, zone_config

    assert get_zone_config() is zone_config
    assert isinstance(zone_config, ZoneConfig)
