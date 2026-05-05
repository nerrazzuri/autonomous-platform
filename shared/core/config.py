from __future__ import annotations

"""Typed quadruped system configuration loaded from YAML with safe defaults."""

from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ConfigError(Exception):
    """Raised when the quadruped configuration file cannot be parsed or validated."""


class AppSection(BaseModel):
    name: str = "agibot-quadruped-platform"
    environment: str = "development"
    timezone: str = "Asia/Singapore"


class QuadrupedSection(BaseModel):
    quadruped_ip: str = "192.168.234.1"
    sdk_port: int = Field(default=43988, ge=1, le=65535)
    sdk_lib_path: str | None = None
    connection_timeout_seconds: float = 5.0
    auto_stand_on_startup: bool = False


class WorkstationSection(BaseModel):
    local_ip: str = "0.0.0.0"
    lan_ip: str = "127.0.0.1"


class DatabaseSection(BaseModel):
    sqlite_path: str = "data/quadruped.db"
    telemetry_retention_hours: int = Field(default=48, gt=0)


class RouteSection(BaseModel):
    routes_file: str = "data/routes.json"
    stations_file: str = "data/stations.json"
    hot_reload_enabled: bool = True


class LogisticsSection(BaseModel):
    routes_file: str = "data/logistics_routes.json"
    allow_placeholder_routes: bool = True


class BatterySection(BaseModel):
    warn_pct: int = Field(default=30, ge=0, le=100)
    critical_pct: int = Field(default=25, ge=0, le=100)
    resume_pct: int = Field(default=90, ge=0, le=100)
    charging_poll_seconds: int = 30

    @model_validator(mode="after")
    def validate_thresholds(self) -> "BatterySection":
        if self.critical_pct > self.warn_pct:
            raise ValueError("battery.critical_pct must be less than or equal to battery.warn_pct")
        if self.resume_pct <= self.warn_pct:
            raise ValueError("battery.resume_pct must be greater than battery.warn_pct")
        return self


class HeartbeatSection(BaseModel):
    interval_seconds: float = Field(default=0.02, gt=0)
    sdk_timeout_seconds: float = Field(default=3.0, gt=0)

    @model_validator(mode="after")
    def validate_timing(self) -> "HeartbeatSection":
        if self.sdk_timeout_seconds <= self.interval_seconds:
            raise ValueError("heartbeat.sdk_timeout_seconds must be greater than heartbeat.interval_seconds")
        return self


class NavigationSection(BaseModel):
    allowed_position_sources: ClassVar[set[str]] = {"odometry", "slam", "qr_anchor"}

    waypoint_tolerance_m: float = Field(default=0.25, gt=0)
    heading_tolerance_deg: float = Field(default=10.0, gt=0)
    max_forward_velocity: float = Field(default=0.35, gt=0)
    max_yaw_rate: float = Field(default=0.6, gt=0)
    obstacle_hold_timeout_seconds: float = Field(default=10.0, gt=0)
    obstacle_stop_distance_m: float = Field(default=0.8, gt=0)
    obstacle_forward_arc_deg: float = Field(default=90.0, gt=0)
    obstacle_stable_clear_seconds: float = Field(default=2.0, ge=0)
    obstacle_min_hold_seconds: float = Field(default=0.5, ge=0)
    obstacle_resume_ramp_seconds: float = Field(default=3.0, ge=0)
    obstacle_repeat_fallback_count: int = Field(default=3, ge=1)
    position_source: str = "odometry"

    @field_validator("position_source")
    @classmethod
    def validate_position_source(cls, value: str) -> str:
        if value not in cls.allowed_position_sources:
            allowed = ", ".join(sorted(cls.allowed_position_sources))
            raise ValueError(f"navigation.position_source must be one of: {allowed}")
        return value


class PatrolSection(BaseModel):
    schedule_enabled: bool = True
    patrol_interval_seconds: int = Field(default=1800, gt=0)
    observation_dwell_seconds: float = Field(default=3.0, gt=0)
    anomaly_cooldown_seconds: float = Field(default=300.0, ge=0)
    max_consecutive_failures: int = Field(default=3, gt=0)
    alert_on_anomaly: bool = True


class VisionSection(BaseModel):
    allowed_providers: ClassVar[set[str]] = {"claude", "local_yolo", "none"}
    allowed_offline_fallback_modes: ClassVar[set[str]] = {"conservative", "local_model", "disabled"}

    enabled: bool = False
    provider: str = "claude"
    claude_model: str = "claude-sonnet-4-20250514"
    claude_max_tokens: int = Field(default=500, gt=0)
    frame_width: int = Field(default=640, gt=0)
    frame_height: int = Field(default=480, gt=0)
    sharpness_threshold: float = Field(default=50.0, ge=0)
    offline_fallback_mode: str = "conservative"
    zones_file: str = "data/zones.yaml"
    api_timeout_seconds: float = Field(default=10.0, gt=0)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in cls.allowed_providers:
            allowed = ", ".join(sorted(cls.allowed_providers))
            raise ValueError(f"vision.provider must be one of: {allowed}")
        return value

    @field_validator("offline_fallback_mode")
    @classmethod
    def validate_offline_fallback_mode(cls, value: str) -> str:
        if value not in cls.allowed_offline_fallback_modes:
            allowed = ", ".join(sorted(cls.allowed_offline_fallback_modes))
            raise ValueError(f"vision.offline_fallback_mode must be one of: {allowed}")
        return value


class TaskScoringSection(BaseModel):
    priority_weight: float = Field(default=100.0, ge=0)
    recency_weight: float = Field(default=10.0, ge=0)
    proximity_weight: float = Field(default=20.0, ge=0)
    direction_bonus: float = Field(default=50.0, ge=0)


class LoggingSection(BaseModel):
    allowed_levels: ClassVar[set[str]] = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    level: str = "INFO"
    log_dir: str = "logs"
    rotating_file_enabled: bool = True
    max_file_mb: int = 10
    backup_count: int = 5
    json_output: bool = True

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in cls.allowed_levels:
            allowed = ", ".join(sorted(cls.allowed_levels))
            raise ValueError(f"logging.level must be one of: {allowed}")
        return normalized


class ApiSection(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    static_ui_dir: str = "ui"
    cors_enabled: bool = True


class AuthSection(BaseModel):
    """Authentication tokens for quadruped operator roles.

    These defaults are placeholders only and must be changed before production use.
    """

    operator_token: str = "change-me-operator"
    qa_token: str = "change-me-qa"
    supervisor_token: str = "change-me-supervisor"

    def get_token_for_role(self, role: str) -> str:
        normalized = role.strip().lower()
        tokens = {
            "operator": self.operator_token,
            "qa": self.qa_token,
            "supervisor": self.supervisor_token,
        }
        if normalized not in tokens:
            raise ValueError("Unsupported role. Expected one of: operator, qa, supervisor")
        return tokens[normalized]


class AlertsSection(BaseModel):
    email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    supervisor_email: str | None = None
    gpio_alert_enabled: bool = False

    @model_validator(mode="after")
    def validate_email_settings(self) -> "AlertsSection":
        if self.email_enabled:
            missing_fields: list[str] = []
            if not self.smtp_host:
                missing_fields.append("alerts.smtp_host")
            if not self.supervisor_email:
                missing_fields.append("alerts.supervisor_email")
            if missing_fields:
                joined = ", ".join(missing_fields)
                raise ValueError(f"{joined} must be set when alerts.email_enabled is true")
        return self


class Ros2Section(BaseModel):
    enabled: bool = False
    scan_topic: str = "/scan"
    pose_topic: str = "/pose"
    odom_topic: str = "/odom"
    odom_frame: str = "odom"
    base_frame: str = "BASE_LINK"
    odom_publish_hz: float = Field(default=20.0, gt=0)


class SpeakerSection(BaseModel):
    enabled: bool = False
    arrival_sound: str = "data/audio/arrival.wav"
    volume_pct: int = Field(default=80, ge=0, le=100)
    player_cmd: str = "aplay"


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    quadruped: QuadrupedSection = Field(default_factory=QuadrupedSection)
    workstation: WorkstationSection = Field(default_factory=WorkstationSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)
    routes: RouteSection = Field(default_factory=RouteSection)
    logistics: LogisticsSection = Field(default_factory=LogisticsSection)
    battery: BatterySection = Field(default_factory=BatterySection)
    heartbeat: HeartbeatSection = Field(default_factory=HeartbeatSection)
    navigation: NavigationSection = Field(default_factory=NavigationSection)
    patrol: PatrolSection = Field(default_factory=PatrolSection)
    vision: VisionSection = Field(default_factory=VisionSection)
    task_scoring: TaskScoringSection = Field(default_factory=TaskScoringSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    api: ApiSection = Field(default_factory=ApiSection)
    auth: AuthSection = Field(default_factory=AuthSection)
    alerts: AlertsSection = Field(default_factory=AlertsSection)
    ros2: Ros2Section = Field(default_factory=Ros2Section)
    speaker: SpeakerSection = Field(default_factory=SpeakerSection)

    def database_path(self) -> Path:
        return Path(self.database.sqlite_path)

    def routes_path(self) -> Path:
        return Path(self.routes.routes_file)

    def stations_path(self) -> Path:
        return Path(self.routes.stations_file)

    def logistics_routes_path(self) -> Path:
        return Path(self.logistics.routes_file)

    def log_path(self) -> Path:
        return Path(self.logging.log_dir)


def _default_config_data() -> dict[str, Any]:
    return AppConfig().model_dump(mode="python")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _resolve_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = Path(_env_path) if (_env_path := _get_env_config_path()) else None
    return env_path if env_path is not None else Path("./config.yaml")


def _get_env_config_path() -> str | None:
    from os import getenv

    return getenv("QUADRUPED_CONFIG_PATH")


def _load_yaml_overrides(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse config file '{config_path}': {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Failed to read config file '{config_path}': {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"Failed to parse config file '{config_path}': top-level YAML content must be a mapping")
    return loaded


def _format_validation_error(config_path: Path, exc: ValidationError) -> str:
    issues: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        issues.append(f"{location}: {error['msg']}")
    details = "; ".join(issues)
    return f"Invalid config file '{config_path}': {details}"


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load quadruped configuration from YAML, environment path, or built-in defaults."""

    config_path = _resolve_config_path(path)
    config_data = _default_config_data()
    overrides = _load_yaml_overrides(config_path)
    merged = _deep_merge(config_data, overrides)
    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(config_path, exc)) from exc


_CONFIG_CACHE: AppConfig | None = None


def get_config() -> AppConfig:
    """Return the cached quadruped configuration, loading it on first access."""

    global _CONFIG_CACHE, CONFIG
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = load_config()
        CONFIG = _CONFIG_CACHE
    return _CONFIG_CACHE


def reload_config(path: str | Path | None = None) -> AppConfig:
    """Reload the quadruped configuration and replace the module cache."""

    global _CONFIG_CACHE, CONFIG
    _CONFIG_CACHE = load_config(path)
    CONFIG = _CONFIG_CACHE
    return _CONFIG_CACHE


CONFIG = get_config()
