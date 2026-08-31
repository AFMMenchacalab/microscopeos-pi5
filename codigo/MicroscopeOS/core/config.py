import json
from dataclasses import dataclass, asdict


@dataclass
class CameraSettings:
    exposure_us: int = 12000
    gain: float = 1.2
    resolution_width: int = 3280
    resolution_height: int = 2464


@dataclass
class TimelapseSettings:
    interval_seconds: int = 60
    duration_seconds: int = 3600
    stabilization_time: float = 0.3
    led_on_time: float = 0.3 

    


@dataclass
class IlluminationSettings:
    gpio_pin: int = 17


class SystemConfig:

    def __init__(self):
        self.camera = CameraSettings()
        self.timelapse = TimelapseSettings()
        self.illumination = IlluminationSettings()

    def save(self, filename="config.json"):
        data = {
            "camera": asdict(self.camera),
            "timelapse": asdict(self.timelapse),
            "illumination": asdict(self.illumination),
        }

        with open(filename, "w") as f:
            json.dump(data, f, indent=4)

        print(f"Configuración guardada en {filename}")

    def load(self, filename="config.json"):
        with open(filename, "r") as f:
            data = json.load(f)

        self.camera = CameraSettings(**data["camera"])
        self.timelapse = TimelapseSettings(**data["timelapse"])
        self.illumination = IlluminationSettings(**data["illumination"])

        print(f"Configuración cargada desde {filename}")
