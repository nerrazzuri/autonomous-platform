#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.provisioning import provision_backend
from shared.provisioning.provision_backend import ProvisioningError
from shared.provisioning.provision_models import ProvisionRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provision a quadruped onto factory WiFi and persist it.")
    parser.add_argument("--dog-ap-ssid", required=True)
    parser.add_argument("--target-wifi-ssid", required=True)
    parser.add_argument("--target-wifi-password", required=True)
    parser.add_argument("--role", required=True, choices=["logistics", "patrol"])
    parser.add_argument("--pc-wifi-iface", required=True)
    parser.add_argument("--robot-id")
    parser.add_argument("--display-name")
    parser.add_argument("--robots-yaml-path", default="data/robots.yaml")
    parser.add_argument("--sdk-lib-path", default="sdk/zsl-1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ssh-user", default="firefly")
    parser.add_argument("--ssh-password")
    return parser


def build_request(args: argparse.Namespace) -> ProvisionRequest:
    return ProvisionRequest(
        dog_ap_ssid=args.dog_ap_ssid,
        target_wifi_ssid=args.target_wifi_ssid,
        target_wifi_password=args.target_wifi_password,
        role=args.role,
        pc_wifi_iface=args.pc_wifi_iface,
        robot_id=args.robot_id,
        ssh_user=args.ssh_user,
        ssh_password=args.ssh_password,
    )


def print_dry_run_summary(request: ProvisionRequest, args: argparse.Namespace) -> None:
    request_data = asdict(request)
    request_data.pop("target_wifi_password", None)
    request_data.pop("ssh_password", None)

    print("Dry-run provisioning request:")
    for key, value in request_data.items():
        print(f"  {key}: {value}")
    print(f"  display_name: {args.display_name}")
    print(f"  robots_yaml_path: {Path(args.robots_yaml_path)}")
    print(f"  sdk_lib_path: {args.sdk_lib_path}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        request = build_request(args)
    except ValueError as exc:
        print(f"Invalid provisioning request: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print_dry_run_summary(request, args)
        return 0

    try:
        result = provision_backend.provision_dog(request)
    except NotImplementedError:
        print("Real provisioning backend is not implemented yet.", file=sys.stderr)
        return 1
    except ProvisioningError as exc:
        print(f"Provisioning failed: {exc}", file=sys.stderr)
        return 1

    if not result.success:
        message = result.message or "Provisioning did not succeed."
        print(message, file=sys.stderr)
        return 1

    try:
        entry = provision_backend.write_robot_entry(
            result,
            args.role,
            Path(args.robots_yaml_path),
            display_name=args.display_name,
            sdk_lib_path=args.sdk_lib_path,
        )
    except ProvisioningError as exc:
        print(f"Failed to persist provisioned robot: {exc}", file=sys.stderr)
        return 1

    print("Provisioning succeeded.")
    print(f"robot_id: {entry.get('robot_id')}")
    print(f"mac: {entry.get('mac')}")
    print(f"quadruped_ip: {entry.get('quadruped_ip')}")
    print(f"robots_yaml_path: {Path(args.robots_yaml_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
