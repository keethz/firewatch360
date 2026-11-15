from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(PROJECT_ROOT / ".env", ".env"), extra="ignore")

    firms_map_key: str = ""
    # AI explanations: Google Gemini API (free tier available); off when the key is empty
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"  # used when the main model is busy or slow
    data_dir: Path = PROJECT_ROOT / "data"
    frontend_origin: str = "http://localhost:5173"

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, v: Path) -> Path:
        # Relative paths (e.g. DATA_DIR=./data in .env) are relative to the project root,
        # not to whichever directory the server was started from.
        return v if v.is_absolute() else (PROJECT_ROOT / v).resolve()

    @property
    def hotspots_glob(self) -> str:
        return str(self.data_dir / "processed" / "hotspots" / "**" / "*.parquet")

    @property
    def coverage_path(self) -> Path:
        return self.data_dir / "processed" / "coverage.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
