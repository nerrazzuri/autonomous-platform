from __future__ import annotations

import importlib
from pathlib import Path
import shlex
import socket
import subprocess
import time
from typing import Any

import yaml

from shared.core.logger import get_logger, redact_sensitive
from shared.provisioning.provision_models import ProvisionRequest, ProvisionResult, WifiNetwork


_VALID_ROLES = {"logistics", "patrol"}
DEFAULT_DOG_AP_IP = "192.168.234.1"
REMOTE_DOG_SCRIPT_PATH = "/usr/local/bin/dog_wifi_provision.sh"
REMOTE_SDK_CONFIG_PATH = "/opt/export/config/sdk_config.yaml"
logger = get_logger(__name__)


class ProvisioningError(RuntimeError):
    """Raised when provisioning data cannot be validated or persisted."""


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProvisioningError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_role(role: object) -> str:
    normalized_role = _require_non_empty_string(role, "role")
    if normalized_role not in _VALID_ROLES:
        raise ProvisioningError("role must be either 'logistics' or 'patrol'")
    return normalized_role


def _normalize_mac(mac_address: object) -> str:
    return _require_non_empty_string(mac_address, "dog_mac").lower()


def _looks_like_robot_ap(ssid: str) -> bool:
    normalized = ssid.strip()
    return normalized.startswith("D1") or "D1-" in normalized or "Agibot" in normalized


def _split_escaped_fields(value: str, expected_fields: int) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaping = False

    for char in value:
        if escaping:
            current.append(char)
            escaping = False
            continue
        if char == "\\":
            escaping = True
            continue
        if char == ":" and len(fields) < expected_fields - 1:
            fields.append("".join(current))
            current = []
            continue
        current.append(char)

    fields.append("".join(current))
    while len(fields) < expected_fields:
        fields.append("")
    return fields[:expected_fields]


def _extract_mac_from_text(value: str | None) -> str | None:
    if value is None:
        return None
    candidates = value.replace("-", ":").split(":")
    for index in range(len(candidates) - 5):
        window = candidates[index : index + 6]
        if all(len(part) == 2 and all(char in "0123456789abcdefABCDEF" for char in part) for part in window):
            return ":".join(part.lower() for part in window)
    return None


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _get_paramiko():
    try:
        return importlib.import_module("paramiko")
    except ImportError as exc:
        raise ProvisioningError("paramiko is required for SSH provisioning but is not installed") from exc


def ssh_connect(host: str, username: str, password: str | None, timeout: float = 10.0):
    paramiko = _get_paramiko()
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            username=username,
            password=password,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
    except Exception as exc:
        raise ProvisioningError(f"SSH connect failed for {host}: {exc}") from exc
    return client


def sftp_put(client: Any, local_path: Path, remote_path: str) -> None:
    try:
        sftp = client.open_sftp()
        try:
            sftp.put(str(local_path), remote_path)
        finally:
            sftp.close()
    except Exception as exc:
        raise ProvisioningError(f"Failed to upload '{local_path}' to '{remote_path}': {exc}") from exc


def run_remote_command(client: Any, command: str, *, timeout: float = 60.0, check: bool = True) -> str:
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        stdout_text = stdout.read().decode("utf-8", errors="replace").strip()
        stderr_text = stderr.read().decode("utf-8", errors="replace").strip()
    except Exception as exc:
        raise ProvisioningError(f"Remote command execution failed: {exc}") from exc

    if check and exit_status != 0:
        message = stderr_text or stdout_text or f"exit status {exit_status}"
        raise ProvisioningError(f"Remote command failed: {message}")
    return stdout_text


def ensure_remote_dog_script(client: Any) -> None:
    local_script_path = Path(__file__).resolve().parents[2] / "scripts" / "dog_wifi_provision.sh"
    if not local_script_path.exists():
        raise ProvisioningError(f"Local provisioning script not found: {local_script_path}")
    sftp_put(client, local_script_path, REMOTE_DOG_SCRIPT_PATH)
    run_remote_command(client, f"sudo chmod +x {REMOTE_DOG_SCRIPT_PATH}")


def get_pc_ip_for_target(target_ip: str) -> str:
    normalized_target_ip = _require_non_empty_string(target_ip, "target_ip")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((normalized_target_ip, 1))
        local_ip = sock.getsockname()[0]
    except OSError as exc:
        raise ProvisioningError(f"Failed to determine local IP for {normalized_target_ip}: {exc}") from exc
    finally:
        sock.close()

    return _require_non_empty_string(local_ip, "pc_ip")


def _load_robot_entries(robots_yaml_path: Path) -> list[dict[str, Any]]:
    if not robots_yaml_path.exists():
        return []

    try:
        loaded = yaml.safe_load(robots_yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProvisioningError(f"Failed to parse '{robots_yaml_path}': {exc}") from exc
    except OSError as exc:
        raise ProvisioningError(f"Failed to read '{robots_yaml_path}': {exc}") from exc

    if loaded is None:
        return []
    if isinstance(loaded, dict):
        robots = loaded.get("robots", [])
    elif isinstance(loaded, list):
        robots = loaded
    else:
        raise ProvisioningError(f"Robot config file '{robots_yaml_path}' must contain a robots list")

    if not isinstance(robots, list):
        raise ProvisioningError(f"Robot config file '{robots_yaml_path}' must contain a robots list")

    normalized_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(robots, start=1):
        if not isinstance(entry, dict):
            raise ProvisioningError(f"Robot entry {index} must be a mapping")
        normalized_entries.append(dict(entry))
    return normalized_entries


def _generate_robot_id(role: str, existing_entries: list[dict[str, Any]]) -> str:
    existing_ids = {
        robot_id
        for entry in existing_entries
        for robot_id in [entry.get("robot_id")]
        if isinstance(robot_id, str) and robot_id.strip()
    }

    suffix = 1
    while True:
        candidate = f"{role}_{suffix:02d}"
        if candidate not in existing_ids:
            return candidate
        suffix += 1


def _ensure_robot_id_is_available(
    robot_id: str,
    existing_entries: list[dict[str, Any]],
    current_entry: dict[str, Any] | None,
) -> None:
    for entry in existing_entries:
        if entry is current_entry:
            continue
        if entry.get("robot_id") == robot_id:
            raise ProvisioningError(f"robot_id '{robot_id}' is already assigned to another robot")


def _find_entry_by_mac(existing_entries: list[dict[str, Any]], mac_address: str) -> dict[str, Any] | None:
    for entry in existing_entries:
        entry_mac = entry.get("mac")
        if isinstance(entry_mac, str) and entry_mac.strip().lower() == mac_address:
            return entry
    return None


def write_robot_entry(
    result: ProvisionResult,
    role: str,
    robots_yaml_path: Path,
    *,
    display_name: str | None = None,
    sdk_lib_path: str = "sdk/zsl-1",
) -> dict[str, Any]:
    if not isinstance(result, ProvisionResult):
        raise ProvisioningError("result must be a ProvisionResult")
    if not result.success:
        raise ProvisioningError("Provision result must indicate success before writing")

    normalized_role = _validate_role(role)
    normalized_mac = _normalize_mac(result.dog_mac)
    normalized_ip = _require_non_empty_string(result.dog_ip, "dog_ip")
    normalized_sdk_lib_path = _require_non_empty_string(sdk_lib_path, "sdk_lib_path")
    normalized_display_name = (
        _require_non_empty_string(display_name, "display_name") if display_name is not None else None
    )

    existing_entries = _load_robot_entries(robots_yaml_path)
    matching_entry = _find_entry_by_mac(existing_entries, normalized_mac)

    explicit_robot_id = (
        _require_non_empty_string(result.robot_id, "robot_id") if result.robot_id is not None else None
    )
    if explicit_robot_id is not None:
        robot_id = explicit_robot_id
    elif matching_entry is not None and isinstance(matching_entry.get("robot_id"), str):
        robot_id = matching_entry["robot_id"].strip()
    else:
        robot_id = _generate_robot_id(normalized_role, existing_entries)

    _ensure_robot_id_is_available(robot_id, existing_entries, matching_entry)

    if matching_entry is None:
        entry: dict[str, Any] = {
            "robot_id": robot_id,
            "mac": normalized_mac,
            "quadruped_ip": normalized_ip,
            "role": normalized_role,
            "sdk_lib_path": normalized_sdk_lib_path,
            "enabled": True,
        }
        if normalized_display_name is not None:
            entry["display_name"] = normalized_display_name
        existing_entries.append(entry)
    else:
        matching_entry["robot_id"] = robot_id
        matching_entry["mac"] = normalized_mac
        matching_entry["quadruped_ip"] = normalized_ip
        matching_entry["role"] = normalized_role
        matching_entry["sdk_lib_path"] = normalized_sdk_lib_path
        matching_entry["enabled"] = bool(matching_entry.get("enabled", True))
        if normalized_display_name is not None:
            matching_entry["display_name"] = normalized_display_name
        entry = matching_entry

    robots_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        robots_yaml_path.write_text(
            yaml.safe_dump({"robots": existing_entries}, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProvisioningError(f"Failed to write '{robots_yaml_path}': {exc}") from exc

    logger.info(
        "robots.yaml write succeeded",
        extra={
            "component": "provisioning",
            "robot_id": robot_id,
            "role": normalized_role,
            "status": "written",
            "path": str(robots_yaml_path),
        },
    )
    return dict(entry)


def list_robot_entries(robots_yaml_path: Path) -> list[dict[str, Any]]:
    return [dict(entry) for entry in _load_robot_entries(robots_yaml_path)]


def remove_robot_entry(robot_id: str, robots_yaml_path: Path) -> dict[str, Any]:
    normalized_robot_id = _require_non_empty_string(robot_id, "robot_id")
    existing_entries = _load_robot_entries(robots_yaml_path)

    removed_entry: dict[str, Any] | None = None
    kept_entries: list[dict[str, Any]] = []
    for entry in existing_entries:
        if entry.get("robot_id") == normalized_robot_id and removed_entry is None:
            removed_entry = dict(entry)
            continue
        kept_entries.append(entry)

    if removed_entry is None:
        raise ProvisioningError(f"Unknown robot_id: {normalized_robot_id}")

    robots_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        robots_yaml_path.write_text(
            yaml.safe_dump({"robots": kept_entries}, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProvisioningError(f"Failed to write '{robots_yaml_path}': {exc}") from exc

    return removed_entry


def scan_wifi_networks() -> list[WifiNetwork]:
    logger.info("WiFi scan started", extra={"component": "provisioning", "event_type": "wifi_scan_started"})
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        logger.warning("WiFi scan failed", extra={"component": "provisioning", "status": "failed", "reason": "nmcli_missing"})
        raise ProvisioningError("nmcli is required for WiFi scanning but is not installed") from exc
    except subprocess.CalledProcessError as exc:
        logger.warning("WiFi scan failed", extra={"component": "provisioning", "status": "failed"})
        raise ProvisioningError(f"nmcli WiFi scan failed: {exc.stderr or exc.stdout or exc}") from exc

    networks: list[WifiNetwork] = []
    for raw_line in result.stdout.splitlines():
        if not raw_line.strip():
            continue
        ssid, signal, security = _split_escaped_fields(raw_line, 3)
        if not ssid.strip():
            continue
        signal_value = int(signal) if signal.strip().isdigit() else None
        security_value = security.strip() or None
        networks.append(
            WifiNetwork(
                ssid=ssid.strip(),
                signal=signal_value,
                security=security_value,
                is_robot_ap=_looks_like_robot_ap(ssid),
            )
        )
    logger.info(
        "WiFi scan succeeded",
        extra={"component": "provisioning", "status": "succeeded", "network_count": len(networks)},
    )
    return networks


def find_ip_by_mac(
    mac_address: str,
    *,
    interface: str | None = None,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
) -> str:
    normalized_mac = _normalize_mac(mac_address)
    logger.info(
        "Robot IP discovery started",
        extra={"component": "provisioning", "mac": normalized_mac, "event_type": "ip_discovery_started"},
    )
    start = time.monotonic()

    while True:
        try:
            result = subprocess.run(
                ["ip", "neigh"],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise ProvisioningError("ip command is required for neighbor discovery but is not installed") from exc
        except subprocess.CalledProcessError as exc:
            raise ProvisioningError(f"ip neigh failed: {exc.stderr or exc.stdout or exc}") from exc

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[1] != "dev":
                continue
            ip_address = parts[0]
            dev_name = parts[2]
            if interface is not None and dev_name != interface:
                continue
            if "lladdr" not in parts:
                continue
            lladdr_index = parts.index("lladdr")
            if lladdr_index + 1 >= len(parts):
                continue
            if parts[lladdr_index + 1].lower() == normalized_mac:
                logger.info(
                    "Robot IP discovery succeeded",
                    extra={"component": "provisioning", "mac": normalized_mac, "status": "succeeded", "robot_ip": ip_address},
                )
                return ip_address

        if time.monotonic() - start > timeout:
            logger.warning(
                "Robot IP discovery failed",
                extra={"component": "provisioning", "mac": normalized_mac, "status": "failed"},
            )
            raise ProvisioningError(f"Timed out waiting to discover IP for MAC {normalized_mac}")
        time.sleep(poll_interval)


def patch_sdk_config(
    robot_ip: str,
    pc_ip: str,
    *,
    username: str = "firefly",
    password: str | None = None,
    timeout: float = 10.0,
) -> None:
    normalized_robot_ip = _require_non_empty_string(robot_ip, "robot_ip")
    normalized_pc_ip = _require_non_empty_string(pc_ip, "pc_ip")
    logger.info(
        "SDK config patch started",
        extra={"component": "provisioning", "robot_ip": normalized_robot_ip, "pc_ip": normalized_pc_ip},
    )
    client = ssh_connect(normalized_robot_ip, username, password, timeout=timeout)
    backup_command = (
        f"sudo cp -p {REMOTE_SDK_CONFIG_PATH} "
        f"{REMOTE_SDK_CONFIG_PATH}.bak.$(date +%Y%m%d%H%M%S)"
    )
    patch_command = f"""sudo python3 - <<'PY'
from pathlib import Path

path = Path("{REMOTE_SDK_CONFIG_PATH}")
content = path.read_text(encoding="utf-8")
lines = content.splitlines()
updated_lines = []
replaced = False
for line in lines:
    if line.strip().startswith("target_ip:"):
        updated_lines.append("target_ip: {normalized_pc_ip}")
        replaced = True
    else:
        updated_lines.append(line)
if not replaced:
    updated_lines.append("target_ip: {normalized_pc_ip}")
path.write_text("\\n".join(updated_lines) + "\\n", encoding="utf-8")
PY"""
    try:
        run_remote_command(client, backup_command, timeout=timeout)
        run_remote_command(client, patch_command, timeout=timeout)
    finally:
        client.close()
    logger.info(
        "SDK config patch succeeded",
        extra={"component": "provisioning", "robot_ip": normalized_robot_ip, "pc_ip": normalized_pc_ip, "status": "succeeded"},
    )


def _safe_remote_read(client: Any, path: str) -> str | None:
    try:
        value = run_remote_command(client, f"cat {shlex.quote(path)}", timeout=10.0)
    except ProvisioningError:
        return None
    return value.strip() or None


def provision_dog(request: ProvisionRequest) -> ProvisionResult:
    try:
        request = request if isinstance(request, ProvisionRequest) else ProvisionRequest(**request)
        logger.info(
            "Provisioning job started",
            extra=redact_sensitive(
                {
                    "component": "provisioning",
                    "event_type": "provisioning_started",
                    "robot_id": request.robot_id,
                    "role": request.role,
                    "dog_ap_ssid": request.dog_ap_ssid,
                    "target_wifi_ssid": request.target_wifi_ssid,
                    "pc_wifi_iface": request.pc_wifi_iface,
                    "ssh_user": request.ssh_user,
                }
            ),
        )
        logger.info(
            "Robot AP SSH attempt",
            extra={"component": "provisioning", "robot_id": request.robot_id, "status": "ssh_connect_attempt"},
        )
        client = ssh_connect(DEFAULT_DOG_AP_IP, request.ssh_user, request.ssh_password, timeout=10.0)
        try:
            logger.info("Provisioning script upload started", extra={"component": "provisioning", "robot_id": request.robot_id})
            ensure_remote_dog_script(client)
            logger.info("Provisioning script upload succeeded", extra={"component": "provisioning", "robot_id": request.robot_id})
            provision_command = (
                f"sudo {REMOTE_DOG_SCRIPT_PATH} "
                f"{_shell_quote(request.target_wifi_ssid)} "
                f"{_shell_quote(request.target_wifi_password)}"
            )
            logger.info(
                "Remote provisioning command started",
                extra={"component": "provisioning", "robot_id": request.robot_id, "status": "remote_command_started"},
            )
            try:
                run_remote_command(client, provision_command, timeout=180.0, check=False)
            except ProvisioningError:
                pass
            logger.info(
                "Remote provisioning command completed",
                extra={"component": "provisioning", "robot_id": request.robot_id, "status": "remote_command_completed"},
            )
            dog_mac = _safe_remote_read(client, "/tmp/dog_mac")
            dog_ip = _safe_remote_read(client, "/tmp/dog_ip")
        finally:
            client.close()

        normalized_mac = _normalize_mac(dog_mac or _extract_mac_from_text(request.dog_ap_ssid))
        resolved_ip = (dog_ip or "").strip() or find_ip_by_mac(
            normalized_mac,
            interface=request.pc_wifi_iface,
            timeout=30.0,
            poll_interval=1.0,
        )
        pc_ip = get_pc_ip_for_target(resolved_ip)
        patch_sdk_config(resolved_ip, pc_ip, username=request.ssh_user, password=request.ssh_password)
        logger.info(
            "Provisioning job succeeded",
            extra={
                "component": "provisioning",
                "robot_id": request.robot_id,
                "role": request.role,
                "dog_mac": normalized_mac,
                "dog_ip": resolved_ip,
                "pc_ip": pc_ip,
                "status": "succeeded",
            },
        )
        return ProvisionResult(
            success=True,
            robot_id=request.robot_id,
            dog_mac=normalized_mac,
            dog_ip=resolved_ip,
            pc_ip=pc_ip,
            role=request.role,
            message="Provisioning complete",
        )
    except ProvisioningError as exc:
        logger.warning(
            "Provisioning job failed",
            extra=redact_sensitive(
                {
                    "component": "provisioning",
                    "robot_id": getattr(request, "robot_id", None),
                    "role": getattr(request, "role", None),
                    "status": "failed",
                    "error_message": str(exc),
                }
            ),
        )
        return ProvisionResult(
            success=False,
            robot_id=request.robot_id,
            role=request.role,
            message=str(exc),
        )
