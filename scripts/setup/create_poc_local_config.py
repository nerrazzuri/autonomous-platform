#!/usr/bin/env python3
from __future__ import annotations

"""Create an uncommitted local POC config with generated auth tokens."""

import argparse
import os
import secrets
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only without project deps installed
    yaml = None


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = ROOT / "apps/logistics/config/logistics_demo_config.yaml"
DEFAULT_OUTPUT = ROOT / "config.local.yaml"

PLACEHOLDER_VALUES = {
    "__OPERATOR_TOKEN__",
    "__QA_TOKEN__",
    "__SUPERVISOR_TOKEN__",
    "change-me-operator",
    "change-me-qa",
    "change-me-supervisor",
    "change-me-internal",
    "__INTERNAL_TOKEN__",
    "__INTERNAL_SECRET__",
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if yaml is None:
        print("PyYAML is required. Run ./scripts/setup/setup_python_env.sh first.", file=sys.stderr)
        return 1

    template_path = Path(args.template)
    output_path = Path(args.output)

    if not template_path.exists():
        print(f"Template config not found: {template_path}", file=sys.stderr)
        return 1
    if output_path.exists() and not args.force:
        print(f"Refusing to overwrite existing config: {output_path}. Use --force to replace it.", file=sys.stderr)
        return 1

    try:
        config = _load_yaml(template_path)
    except Exception as exc:
        print(f"Failed to read template config: {exc}", file=sys.stderr)
        return 1

    tokens = {
        "OPERATOR_TOKEN": secrets.token_urlsafe(32),
        "QA_TOKEN": secrets.token_urlsafe(32),
        "SUPERVISOR_TOKEN": secrets.token_urlsafe(32),
        "INTERNAL_TOKEN": secrets.token_urlsafe(32),
    }

    config = _replace_placeholder_values(config, tokens)
    _set_auth_tokens(config, tokens)
    _apply_poc_defaults(config, args)

    try:
        _write_yaml(output_path, config)
    except Exception as exc:
        print(f"Failed to write local config: {exc}", file=sys.stderr)
        return 1

    print(f"Created local POC config: {output_path}")
    print("Generated operator, QA, and supervisor tokens.")
    if args.print_tokens:
        print("Store these securely. They are also written to config.local.yaml. Do not commit this file.")
        print(f"OPERATOR_TOKEN={tokens['OPERATOR_TOKEN']}")
        print(f"QA_TOKEN={tokens['QA_TOKEN']}")
        print(f"SUPERVISOR_TOKEN={tokens['SUPERVISOR_TOKEN']}")
        if _config_has_internal_secret(config):
            print(f"INTERNAL_TOKEN={tokens['INTERNAL_TOKEN']}")
    else:
        print("Tokens were not printed. Use --print-tokens only in a private terminal if you need to view them.")

    print("Next steps:")
    print(f"1. Edit {output_path} and fill any remaining site-specific IP fields.")
    print("2. Run ./scripts/check_runtime_env.sh")
    print(f"3. Run APP_CONFIG={output_path} DRY_RUN=1 ./scripts/start_logistics_dev.sh")
    print(f"4. Start backend with APP_CONFIG={output_path} ./scripts/start_logistics_dev.sh")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="Template YAML config path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output local YAML config path")
    parser.add_argument("--workstation-ip", help="Set workstation.local_ip and workstation.lan_ip")
    parser.add_argument("--quadruped-ip", help="Set quadruped.quadruped_ip")
    parser.add_argument("--sdk-lib-path", help="Set quadruped.sdk_lib_path")
    parser.add_argument("--speaker-enabled", choices=("true", "false"), help="Set speaker.enabled")
    parser.add_argument(
        "--allow-placeholder-routes",
        choices=("true", "false"),
        help="Set logistics.allow_placeholder_routes",
    )
    parser.add_argument(
        "--position-source",
        choices=("slam", "odometry"),
        default="slam",
        help="Set navigation.position_source",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite output if it already exists")
    parser.add_argument("--print-tokens", action="store_true", help="Print generated tokens once")
    return parser


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError("template must contain a YAML mapping")
    return payload


def _write_yaml(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        print(f"WARN could not chmod 600 {path}: {exc}", file=sys.stderr)


def _replace_placeholder_values(value: Any, tokens: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_placeholder_values(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholder_values(item, tokens) for item in value]
    if isinstance(value, str):
        replacements = {
            "__OPERATOR_TOKEN__": tokens["OPERATOR_TOKEN"],
            "__QA_TOKEN__": tokens["QA_TOKEN"],
            "__SUPERVISOR_TOKEN__": tokens["SUPERVISOR_TOKEN"],
            "change-me-operator": tokens["OPERATOR_TOKEN"],
            "change-me-qa": tokens["QA_TOKEN"],
            "change-me-supervisor": tokens["SUPERVISOR_TOKEN"],
            "change-me-internal": tokens["INTERNAL_TOKEN"],
            "__INTERNAL_TOKEN__": tokens["INTERNAL_TOKEN"],
            "__INTERNAL_SECRET__": tokens["INTERNAL_TOKEN"],
        }
        if value in replacements:
            return replacements[value]
    return value


def _set_auth_tokens(config: dict[str, Any], tokens: dict[str, str]) -> None:
    auth = config.setdefault("auth", {})
    if isinstance(auth, dict):
        auth["operator_token"] = tokens["OPERATOR_TOKEN"]
        auth["qa_token"] = tokens["QA_TOKEN"]
        auth["supervisor_token"] = tokens["SUPERVISOR_TOKEN"]


def _apply_poc_defaults(config: dict[str, Any], args: argparse.Namespace) -> None:
    ros2 = config.setdefault("ros2", {})
    if isinstance(ros2, dict):
        ros2["enabled"] = True
        ros2["scan_topic"] = "/scan"
        ros2["pose_topic"] = "/pose"
        ros2["odom_topic"] = "/odom"
        ros2["odom_frame"] = "odom"
        ros2["base_frame"] = "BASE_LINK"

    navigation = config.setdefault("navigation", {})
    if isinstance(navigation, dict):
        navigation["position_source"] = args.position_source

    logistics = config.setdefault("logistics", {})
    if isinstance(logistics, dict):
        logistics["allow_placeholder_routes"] = _bool_arg(args.allow_placeholder_routes, default=True)

    if args.workstation_ip:
        workstation = config.setdefault("workstation", {})
        if isinstance(workstation, dict):
            workstation["local_ip"] = args.workstation_ip
            workstation["lan_ip"] = args.workstation_ip

    if args.quadruped_ip:
        quadruped = config.setdefault("quadruped", {})
        if isinstance(quadruped, dict):
            quadruped["quadruped_ip"] = args.quadruped_ip

    if args.sdk_lib_path:
        quadruped = config.setdefault("quadruped", {})
        if isinstance(quadruped, dict):
            quadruped["sdk_lib_path"] = args.sdk_lib_path

    if args.speaker_enabled is not None:
        speaker = config.setdefault("speaker", {})
        if isinstance(speaker, dict):
            speaker["enabled"] = _bool_arg(args.speaker_enabled, default=False)


def _bool_arg(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() == "true"


def _config_has_internal_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if "internal" in lowered_key and ("token" in lowered_key or "secret" in lowered_key):
                return True
            if _config_has_internal_secret(item):
                return True
    elif isinstance(value, list):
        return any(_config_has_internal_secret(item) for item in value)
    elif isinstance(value, str):
        return value in PLACEHOLDER_VALUES and "INTERNAL" in value.upper()
    return False


if __name__ == "__main__":
    raise SystemExit(main())
