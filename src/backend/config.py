"""Database environment configuration.

Selected with environment variables:

- `COLLECTIVIZE_ENV`: `test` (default) or `production`. Defaults to test so
  nothing can write real data by accident.
- `COLLECTIVIZE_DB_PATH`: optional override for the database file.

Default files: `data/test.db` (mock data) and `data/production.db` (real data).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
ENV_VAR = "COLLECTIVIZE_ENV"
DB_PATH_VAR = "COLLECTIVIZE_DB_PATH"


class Environment(Enum):
    TEST = "test"
    PRODUCTION = "production"


class ConfigError(ValueError):
    """The environment configuration is invalid."""


@dataclass(frozen=True)
class DatabaseConfig:
    environment: Environment
    path: Path

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


DEFAULT_PATHS: dict[Environment, Path] = {
    Environment.TEST: DATA_DIR / "test.db",
    Environment.PRODUCTION: DATA_DIR / "production.db",
}


def load_config(env: Mapping[str, str] | None = None) -> DatabaseConfig:
    env = os.environ if env is None else env
    raw = env.get(ENV_VAR, Environment.TEST.value).strip().lower()
    try:
        environment = Environment(raw)
    except ValueError:
        allowed = ", ".join(e.value for e in Environment)
        raise ConfigError(f"{ENV_VAR} must be one of: {allowed} (got {raw!r})") from None
    override = env.get(DB_PATH_VAR, "").strip()
    path = Path(override) if override else DEFAULT_PATHS[environment]
    return DatabaseConfig(environment=environment, path=path)
