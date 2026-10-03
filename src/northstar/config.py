"""Configuration. The optional .env file is parsed as data, never executed."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env line {number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    api_token: str
    database_path: str
    webhook_url: str
    retry_count: int = 2
    timeout_seconds: float = 0.5
    retry_delay_seconds: float = 0.05
    log_level: str = "INFO"

    @classmethod
    def load(cls, env_path: str = ".env") -> "Config":
        file_values = read_env_file(Path(env_path))
        def value(key: str, default: str) -> str:
            return os.environ.get(key, file_values.get(key, default))
        config = cls(
            api_token=value("DEMO_API_TOKEN", ""),
            database_path=value("DEMO_DATABASE_PATH", "./work/demo.sqlite3"),
            webhook_url=value("DEMO_WEBHOOK_URL", "http://127.0.0.1:8001/webhook/success"),
            retry_count=int(value("DEMO_RETRY_COUNT", "2")),
            timeout_seconds=float(value("DEMO_TIMEOUT_SECONDS", "0.5")),
            retry_delay_seconds=float(value("DEMO_RETRY_DELAY_SECONDS", "0.05")),
            log_level=value("DEMO_LOG_LEVEL", "INFO").upper(),
        )
        if not config.api_token or len(config.api_token) < 12:
            raise ValueError("DEMO_API_TOKEN must contain at least 12 characters")
        if not 0 <= config.retry_count <= 5:
            raise ValueError("DEMO_RETRY_COUNT must be between 0 and 5")
        if not 0 < config.timeout_seconds <= 10 or not 0 <= config.retry_delay_seconds <= 10:
            raise ValueError("Timeout and delay are outside the supported range")
        parsed = urlparse(config.webhook_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Demo webhooks must use a local HTTP receiver")
        if config.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("Unsupported DEMO_LOG_LEVEL")
        return config
