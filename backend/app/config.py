"""Runtime settings, read once from environment variables (see .env.example)."""
import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    simulator_url: str = _env("SIMULATOR_URL", "http://localhost:8000")
    sim_lab_url: str = _env("SIM_LAB_URL", "http://localhost:8001")
    intel_url: str = _env("INTEL_URL", "http://localhost:8090")
    autonomy_mode: str = _env("AUTONOMY_MODE", "SUPERVISED").upper()
    operator_token: str = _env("OPERATOR_TOKEN", "change-me-operator")
    admin_token: str = _env("ADMIN_TOKEN", "change-me-admin")
    horizon_ticks: int = int(_env("PLAN_HORIZON_TICKS", "48"))
    safety_z: float = float(_env("SAFETY_Z", "1.28"))
    constrained_factor: float = float(_env("CONSTRAINED_FACTOR", "0.5"))
    database_path: str = _env("DATABASE_PATH", "./jalani.db")
    azure_endpoint: str = _env("AZURE_OPENAI_ENDPOINT", "")
    azure_key: str = _env("AZURE_OPENAI_API_KEY", "")
    azure_deployment: str = _env("AZURE_OPENAI_DEPLOYMENT", "")
    azure_api_version: str = _env("AZURE_OPENAI_API_VERSION", "2024-10-21")
    git_sha: str = _env("GIT_SHA", "dev")
    poll_seconds: float = float(_env("POLL_SECONDS", "0.4"))
    version: str = "1.0.0"


settings = Settings()
