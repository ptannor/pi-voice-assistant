from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PI_CONFIG_PATH = REPO_ROOT / ".pi-config"


def _read_pi_config() -> dict[str, str]:
    if not PI_CONFIG_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for line in PI_CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class ShabbatConfig:
    # Location is personal -- read from .pi-config (gitignored), never hardcoded here.
    # See README for how to set SHABBAT_GEONAMEID / SHABBAT_ISRAEL.
    geonameid: str | None = None
    israel: bool = True

    candle_lighting_offset_minutes: int = 18  # Hebcal `b` param, passed explicitly
    havdalah_degrees: float = 8.5  # Hebcal `M=on&td=8.5` -- their own default ("three small stars")

    warning_offsets_minutes: tuple[int, ...] = (15, 10, 5)

    assets_dir: Path = field(default_factory=lambda: REPO_ROOT / "assets" / "shabbat")
    cache_path: Path = field(default_factory=lambda: REPO_ROOT / "shabbat_cache.json")
    state_path: Path = field(default_factory=lambda: REPO_ROOT / "shabbat_state.json")
    cache_refresh_days: int = 7
    cache_max_age_days: int = 30  # beyond this, treat cache as untrustworthy -> fail closed

    systemd_unit: str = "pi-voice-assistant"

    def message_wav(self, name: str) -> Path:
        return self.assets_dir / f"{name}.wav"


def load_config() -> ShabbatConfig:
    values = _read_pi_config()
    geonameid = values.get("SHABBAT_GEONAMEID")
    israel_raw = values.get("SHABBAT_ISRAEL")
    kwargs: dict = {}
    if geonameid:
        kwargs["geonameid"] = geonameid
    if israel_raw is not None:
        kwargs["israel"] = israel_raw.strip().lower() in ("1", "true", "yes", "on")
    return ShabbatConfig(**kwargs)
