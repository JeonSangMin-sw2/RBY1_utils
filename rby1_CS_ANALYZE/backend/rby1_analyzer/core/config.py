from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


def default_data_root(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    configured = (
        environment.get("RBY1_CS_ANALYZER_DATA_ROOT")
        or environment.get("RBY1_CS_ANALYZER_V5_DATA_ROOT")
        or environment.get("RBY1_CS_ANALYZER_V4_DATA_ROOT")
        or environment.get("RBY1_CS_ANALYZER_V3_DATA_ROOT")
        or environment.get("RBY1_CS_ANALYZER_V2_DATA_ROOT")
    )
    if configured:
        return Path(configured).expanduser()

    # 1. 개발/소스코드 환경에서는 레포지토리 내의 ./data 디렉터리를 기본값으로 사용
    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "backend").is_dir():
        return repo_root / "data"
    if (Path.cwd() / "backend").is_dir() or (Path.cwd() / "data").is_dir():
        return Path.cwd() / "data"

    # 2. 독립 배포 바이너리 환경에서는 V5 표준 전용 디렉터리 사용
    if (os.name if platform_name is None else platform_name) == "nt":
        local_app_data = environment.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        return base / "RB-Y1 CS Analyzer V5"
    return user_home / ".local" / "share" / "rby1-cs-analyzer-v5"


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    host: str = "127.0.0.1"
    port: int = 0
    bootstrap_ttl_seconds: float = 60.0

    @classmethod
    def default(cls) -> "Settings":
        return cls(default_data_root())
