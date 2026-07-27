"""Configuration and paths.

Everything reads from here so that the data directory can be relocated
(e.g. to a mounted volume in CI) without touching collector code.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    fuelcheck_api_key: str = ""
    fuelcheck_api_secret: str = ""
    anthropic_api_key: str = ""
    auscpi_data_dir: str = ""

    @property
    def data_dir(self) -> Path:
        return Path(self.auscpi_data_dir) if self.auscpi_data_dir else REPO_ROOT / "data"

    @property
    def raw_dir(self) -> Path:
        """Immutable, append-only, committed. Never edit or delete anything here."""
        return self.data_dir / "raw"

    @property
    def curated_dir(self) -> Path:
        """Derived and fully rebuildable from raw. Gitignored."""
        return self.data_dir / "curated"

    @property
    def published_dir(self) -> Path:
        """Small tidy outputs intended for humans. Committed."""
        return self.data_dir / "published"

    @property
    def forecast_log(self) -> Path:
        return REPO_ROOT / "forecasts" / "log.csv"


settings = Settings()
