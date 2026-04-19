import yaml
from pathlib import Path
from app.shared.exceptions import ConfigError


class ConfigLoader:
    def __init__(self, config_dir: str = "config"):
        self.base = Path(config_dir)

    def _load(self, filename: str) -> dict:
        path = self.base / filename
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def load_thresholds(self) -> dict:
        return self._load("thresholds.yaml")

    def load_whitelist(self) -> dict:
        return self._load("whitelist.yaml")

    def load_schema(self) -> dict:
        return self._load("schema/tc_oracle.yaml")

    def load_few_shot_seed(self) -> list[dict]:
        data = self._load("few_shot/sql_seed.yaml")
        return data.get("examples", [])

    def load_agents(self) -> dict:
        return self._load("agents.yaml")
