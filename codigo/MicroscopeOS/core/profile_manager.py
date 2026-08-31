import os
from core.config import SystemConfig


class ProfileManager:

    def __init__(self, profiles_dir="profiles"):
        self.profiles_dir = profiles_dir
        os.makedirs(self.profiles_dir, exist_ok=True)

    def list_profiles(self):
        return [
            f.replace(".json", "")
            for f in os.listdir(self.profiles_dir)
            if f.endswith(".json")
        ]

    def load_profile(self, profile_name):
        config = SystemConfig()
        filepath = os.path.join(self.profiles_dir, f"{profile_name}.json")
        config.load(filepath)
        return config

    def save_profile(self, profile_name, config):
        filepath = os.path.join(self.profiles_dir, f"{profile_name}.json")
        config.save(filepath)
        print(f"Perfil '{profile_name}' guardado.")

    def delete_profile(self, profile_name):
        filepath = os.path.join(self.profiles_dir, f"{profile_name}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"Perfil '{profile_name}' eliminado.")
