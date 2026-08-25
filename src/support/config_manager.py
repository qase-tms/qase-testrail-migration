import os
import json


class ConfigManager:

    def __init__(self, config_file = './config.json', env_vars_prefix = 'QASE_'):
        self.config_file = config_file
        self.env_vars_prefix = env_vars_prefix
        self.config = {}

    def load_config(self):
        """Load config.json, failing loudly if it is missing or malformed.

        This used to swallow both cases and leave self.config empty, so a
        missing file surfaced as a 401 deep inside the run instead of an
        error at startup.
        """
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(
                f"Config file not found: {self.config_file}. "
                "Copy config.example.json to config.json and fill it in."
            )
        try:
            with open(self.config_file, "r") as file:
                self.config = json.load(file)
        except json.JSONDecodeError as e:
            raise ValueError(f"{self.config_file} is not valid JSON: {e}") from e

        if not isinstance(self.config, dict):
            raise ValueError(f"{self.config_file} must contain a JSON object.")

    def get(self, key):
        return self._get_config(key)

    def _get_keys(self, config, prefix=""):
        for key, value in config.items():
            if isinstance(value, dict):
                yield from self._get_keys(value, f"{prefix}{key}.")
            else:
                yield f"{prefix}{key}"

    def _set_config(self, key, value):
        keys = key.split(".")
        config = self.config
        for key in keys[:-1]:
            config = config.setdefault(key, {})
        config[keys[-1]] = value

    def _get_config(self, key):
        keys = key.split(".")
        config = self.config
        for key in keys[:-1]:
            config = config.get(key, {})
        return config.get(keys[-1], None)
