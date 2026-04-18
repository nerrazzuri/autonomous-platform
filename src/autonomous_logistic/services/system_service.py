from autonomous_logistic.adapters.robot import RobotAdapter
from autonomous_logistic.config.settings import AppSettings
from autonomous_logistic.core.models import Station
from autonomous_logistic.state.repositories import StationRepository


class SystemService:
    def __init__(self, settings: AppSettings, stations: StationRepository, robot: RobotAdapter) -> None:
        self.settings = settings
        self.stations = stations
        self.robot = robot

    def list_stations(self) -> list[Station]:
        return self.stations.list_all()

    def get_health(self) -> dict:
        return {
            "app_name": self.settings.app_name,
            "app_mode": self.settings.app_mode,
            "robot": self.robot.get_health_status(),
            "capabilities": self.settings.capabilities.to_dict(),
        }

    def get_capabilities(self) -> dict:
        return self.settings.capabilities.to_dict()
