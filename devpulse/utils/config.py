# devpulse/utils/config.py
# TOML-like config file parser using only stdlib configparser.
# Reads ~/.devpulse.toml or .devpulse.toml in current directory.
# Falls back to defaults for all missing keys.
# No pydantic, no marshmallow, no third-party validation.

import os
import configparser
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILENAME = ".devpulse.toml"
_GLOBAL_CONFIG_PATH = os.path.expanduser(f"~/{_DEFAULT_CONFIG_FILENAME}")

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "core": {
        "db_path": os.path.expanduser("~/.devpulse/devpulse.db"),
        "log_level": "WARNING",
        "log_file": "",
        "data_dir": os.path.expanduser("~/.devpulse"),
    },
    "tracker": {
        "idle_timeout_minutes": 10,
        "heartbeat_interval_seconds": 30,
        "auto_pause": True,
        "auto_resume": True,
        "work_start_hour": 8,
        "work_end_hour": 22,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 7820,
        "auth_enabled": False,
        "cors_origins": "*",
        "rate_limit_per_minute": 120,
        "static_dir": "",
    },
    "analyzer": {
        "exclude_patterns": ".git,node_modules,__pycache__,.venv,dist,build",
        "max_file_size_kb": 500,
        "cache_ttl_hours": 24,
        "include_extensions": "",
    },
    "reporter": {
        "output_dir": os.path.expanduser("~/.devpulse/reports"),
        "date_format": "%Y-%m-%d",
        "default_format": "markdown",
    },
    "notifications": {
        "enabled": False,
        "on_idle_pause": True,
        "on_goal_met": True,
    },
}

_ENV_OVERRIDES = {
    ("core", "db_path"): "DEVPULSE_DB_PATH",
    ("server", "host"): "DEVPULSE_SERVER_HOST",
    ("server", "port"): "DEVPULSE_SERVER_PORT",
    ("server", "static_dir"): "DEVPULSE_STATIC_DIR",
}


class ConfigError(Exception):
    pass


class Config:
    """
    Reads a .devpulse.toml config file (INI format with sections).
    Falls back to DEFAULTS for any missing key.
    Provides typed getters: get_str, get_int, get_float, get_bool, get_list.

    Config files use INI syntax:
        [core]
        db_path = /home/user/.devpulse/devpulse.db
        log_level = INFO

        [server]
        port = 7820
        auth_enabled = true
    """

    def __init__(self, path: Optional[str] = None):
        self._parser = configparser.ConfigParser(
            default_section=None,
            allow_no_value=False,
            interpolation=None,
        )
        self._loaded_from: Optional[str] = None
        self._data: Dict[str, Any] = {}
        self._load(path)

        # Preserve parsed values for compatibility with test fixtures.
        for section in self._parser.sections():
            for key, value in self._parser.items(section):
                self._data[key] = value

    def _load(self, path: Optional[str]) -> None:
        candidates = []
        if path:
            candidates.append(path)
        # Local directory first, then home
        local = os.path.join(os.getcwd(), _DEFAULT_CONFIG_FILENAME)
        if local not in candidates:
            candidates.append(local)
        if _GLOBAL_CONFIG_PATH not in candidates:
            candidates.append(_GLOBAL_CONFIG_PATH)

        for candidate in candidates:
            if os.path.isfile(candidate):
                try:
                    self._parser.read(candidate, encoding="utf-8")
                    self._loaded_from = candidate
                    logger.debug(f"Config loaded from {candidate}")
                    return
                except configparser.Error as e:
                    raise ConfigError(
                        f"Failed to parse config file {candidate}: {e}"
                    )

        logger.debug("No config file found. Using defaults.")

    def _get_raw(self, section: str, key: str) -> Optional[str]:
        env_name = _ENV_OVERRIDES.get((section, key))
        if env_name is not None:
            env_value = os.environ.get(env_name)
            if env_value is not None:
                return env_value

        if key in self._data:
            return str(self._data[key])
        try:
            return self._parser.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return None

    def _default(self, section: str, key: str) -> Any:
        sec = DEFAULTS.get(section, {})
        return sec.get(key)

    def get_str(self, section: str, key: str,
                fallback: Optional[str] = None) -> str:
        raw = self._get_raw(section, key)
        if raw is not None:
            return raw.strip()
        default = self._default(section, key)
        if default is not None:
            return str(default)
        if fallback is not None:
            return fallback
        return ""

    def get_int(self, section: str, key: str,
                fallback: Optional[int] = None) -> int:
        raw = self._get_raw(section, key)
        if raw is not None:
            try:
                return int(raw.strip())
            except ValueError:
                logger.warning(
                    f"Config [{section}].{key} = {raw!r} is not an integer"
                )
        default = self._default(section, key)
        if isinstance(default, int):
            return default
        if fallback is not None:
            return fallback
        return 0

    def get_float(self, section: str, key: str,
                  fallback: Optional[float] = None) -> float:
        raw = self._get_raw(section, key)
        if raw is not None:
            try:
                return float(raw.strip())
            except ValueError:
                logger.warning(
                    f"Config [{section}].{key} = {raw!r} is not a float"
                )
        default = self._default(section, key)
        if isinstance(default, (int, float)):
            return float(default)
        if fallback is not None:
            return fallback
        return 0.0

    def get_bool(self, section: str, key: str,
                 fallback: Optional[bool] = None) -> bool:
        raw = self._get_raw(section, key)
        if raw is not None:
            val = raw.strip().lower()
            if val in ("true", "1", "yes", "on"):
                return True
            if val in ("false", "0", "no", "off"):
                return False
            logger.warning(
                f"Config [{section}].{key} = {raw!r} is not a boolean"
            )
        default = self._default(section, key)
        if isinstance(default, bool):
            return default
        if fallback is not None:
            return fallback
        return False

    def get_list(self, section: str, key: str,
                 delimiter: str = ",") -> list:
        raw = self._get_raw(section, key)
        if raw is not None:
            return [x.strip() for x in raw.split(delimiter) if x.strip()]
        default = self._default(section, key)
        if isinstance(default, str):
            return [x.strip() for x in default.split(delimiter) if x.strip()]
        if isinstance(default, list):
            return default
        return []

    # Convenience properties for most-used config values

    @property
    def db_path(self) -> str:
        return self.get_str("core", "db_path")

    @property
    def data_dir(self) -> str:
        return self.get_str("core", "data_dir")

    @property
    def log_level(self) -> str:
        return self.get_str("core", "log_level").upper()

    @property
    def server_host(self) -> str:
        return self.get_str("server", "host")

    @property
    def server_port(self) -> int:
        return self.get_int("server", "port")

    @property
    def auth_enabled(self) -> bool:
        return self.get_bool("server", "auth_enabled")

    @property
    def idle_timeout(self) -> int:
        return self.get_int("tracker", "idle_timeout_minutes")

    @property
    def heartbeat_interval(self) -> int:
        return self.get_int("tracker", "heartbeat_interval_seconds")

    @property
    def exclude_patterns(self) -> list:
        return self.get_list("analyzer", "exclude_patterns")

    @property
    def cache_ttl_hours(self) -> int:
        return self.get_int("analyzer", "cache_ttl_hours")

    def ensure_data_dir(self) -> None:
        """Create the data directory if it doesn't exist."""
        d = self.data_dir
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            logger.info(f"Created data directory: {d}")

    def ensure_reports_dir(self) -> None:
        d = self.get_str("reporter", "output_dir")
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)

    def as_dict(self) -> Dict[str, Dict[str, Any]]:
        """Return all resolved config values as a nested dict."""
        result = {}
        for section, keys in DEFAULTS.items():
            result[section] = {}
            for key in keys:
                result[section][key] = self._get_raw(section, key) \
                    or self._default(section, key)
        return result

    def loaded_from(self) -> Optional[str]:
        return self._loaded_from

    def __repr__(self) -> str:
        src = self._loaded_from or "defaults only"
        return f"Config(source={src!r})"


def setup_logging(config: Config) -> None:
    """
    Configure Python's logging module based on config settings.
    Call once at startup before any other module logs.
    """
    import logging.handlers

    level_str = config.log_level
    level = getattr(logging, level_str, logging.WARNING)

    handlers = [logging.StreamHandler(sys.stdout if level <= logging.DEBUG
                                      else None)]

    log_file = config.get_str("core", "log_file")
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


import sys  # noqa: E402 — needed for setup_logging handler