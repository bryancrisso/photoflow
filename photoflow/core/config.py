"""Configuration loader for ~/.config/photoflow/profiles.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


DEFAULT_PROFILES_PATH = Path("~/.config/photoflow/profiles.yaml")


class DigitalCameraProfile(BaseModel):
    """Digital camera body profile."""

    make: str
    model: str
    mount: str | None = None


class FilmCameraProfile(BaseModel):
    """Film camera body profile."""

    make: str
    model: str
    format: str = "35mm"
    mount: str | None = None


class CamerasInventory(BaseModel):
    """Decoupled camera body inventory split into digital and film."""

    digital: dict[str, DigitalCameraProfile] = Field(default_factory=dict)
    film: dict[str, FilmCameraProfile] = Field(default_factory=dict)


class FilmStockProfile(BaseModel):
    """Film stock definition."""

    brand: str
    name: str
    type: str = "Color Negative"
    iso: int = 200


class ProfilesConfig(BaseModel):
    """Root configuration model for ~/.config/photoflow/profiles.yaml."""

    archive_root: str = "~/PhotoWorkspace/Archive"
    digikam_db: str = "~/PhotoWorkspace/digikam_db/digikam4.db"
    rclone_cache_dir: str = "~/PhotoWorkspace/.rclone_cache/vfs/gdrive/PhotoArchive"
    rclone_rc_url: str = "http://127.0.0.1:5572"

    cameras: CamerasInventory = Field(default_factory=CamerasInventory)
    lenses: dict[str, str] = Field(default_factory=dict)
    film_stocks: dict[str, FilmStockProfile] = Field(default_factory=dict)
    labs: dict[str, str] = Field(default_factory=dict)

    @field_validator("lenses", mode="before")
    @classmethod
    def _normalize_lenses(cls, v: Any) -> dict[str, str]:
        if not v:
            return {}
        if isinstance(v, dict):
            normalized: dict[str, str] = {}
            for k, val in v.items():
                if isinstance(val, str):
                    normalized[str(k)] = val
                elif isinstance(val, dict):
                    normalized[str(k)] = str(val.get("name") or val.get("model") or k)
                else:
                    normalized[str(k)] = str(val)
            return normalized
        return v

    @property
    def archive_root_path(self) -> Path:
        """Expanded Path to the archive root directory."""
        return Path(self.archive_root).expanduser()

    @property
    def digikam_db_path(self) -> Path:
        """Expanded Path to digikam4.db."""
        return Path(self.digikam_db).expanduser()

    @property
    def rclone_cache_path(self) -> Path:
        """Expanded Path to the rclone VFS cache directory."""
        return Path(self.rclone_cache_dir).expanduser()

    def resolve_lens(self, key_or_name: str) -> str:
        """Resolve a lens key from profiles.yaml or return the literal string."""
        return self.lenses.get(key_or_name, key_or_name)

    def resolve_lab(self, key_or_name: str) -> str:
        """Resolve a processing lab key from profiles.yaml or return the literal string."""
        return self.labs.get(key_or_name, key_or_name)


def load_profiles(config_path: Path | str | None = None) -> ProfilesConfig:
    """Load and validate ~/.config/photoflow/profiles.yaml."""
    if config_path is None:
        env_path = os.environ.get("PHOTOFLOW_PROFILES") or os.environ.get("PHOTOFLOW_CONFIG")
        path = Path(env_path).expanduser() if env_path else DEFAULT_PROFILES_PATH.expanduser()
    else:
        path = Path(config_path).expanduser()

    if not path.exists():
        return ProfilesConfig()

    with path.open("r", encoding="utf-8") as fh:
        raw_data = yaml.safe_load(fh) or {}

    return ProfilesConfig.model_validate(raw_data)
