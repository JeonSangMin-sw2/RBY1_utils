from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


def _current_or_legacy(current: Path, legacy_roots: list[Path]) -> Path:
    if current.exists():
        return current
    return next((path for path in legacy_roots if path.exists()), current)


def default_data_root(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    configured = (
        environment.get("RBY1_CS_ANALYZER_V4_DATA_ROOT")
        or environment.get("RBY1_CS_ANALYZER_V3_DATA_ROOT")
        or environment.get("RBY1_CS_ANALYZER_V2_DATA_ROOT")
    )
    if configured:
        return Path(configured).expanduser()

    for candidate in (
        Path.cwd() / "data",
        Path(__file__).resolve().parents[3] / "data",
    ):
        if candidate.is_dir():
            return candidate

    if (os.name if platform_name is None else platform_name) == "nt":
        local_app_data = environment.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        current = base / "RB-Y1 CS Analyzer V4"
        legacy_roots = [base / "RB-Y1 CS Analyzer V3", base / "RB-Y1 CS Analyzer V2"]
        return _current_or_legacy(current, legacy_roots)
    current = user_home / ".local" / "share" / "rby1-cs-analyzer-v4"
    legacy_roots = [
        user_home / ".local" / "share" / "rby1-cs-analyzer-v3",
        user_home / ".local" / "share" / "rby1-cs-analyzer-v2",
    ]
    return _current_or_legacy(current, legacy_roots)


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    host: str = "127.0.0.1"
    port: int = 0
    bootstrap_ttl_seconds: float = 60.0

    @classmethod
    def default(cls) -> "Settings":
        return cls(default_data_root())
