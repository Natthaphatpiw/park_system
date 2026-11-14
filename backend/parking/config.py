"""Environment configuration; credentials never go to the browser."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    demo_mode: bool = False
    demo_db_path: str = "parking_demo.sqlite3"
    supabase_url: str = ""
    supabase_secret_key: str = ""
    vision_api_key: str = ""
    dashboard_api_key: str = ""
    cooldown_seconds: int = 60
    min_confidence: float = 0.65

    @classmethod
    def from_env(cls):
        demo = os.getenv("DEMO_MODE", "false").lower() == "true"
        if demo and os.getenv("VERCEL"):
            raise RuntimeError("DEMO_MODE uses local SQLite and must not run on Vercel")
        settings = cls(
            demo_mode=demo,
            demo_db_path=os.getenv("DEMO_DB_PATH", "parking_demo.sqlite3"),
            supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
            supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")),
            vision_api_key=os.getenv("VISION_API_KEY", ""),
            dashboard_api_key=os.getenv("DASHBOARD_API_KEY", ""),
            cooldown_seconds=int(os.getenv("SCAN_COOLDOWN_SECONDS", "60")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.65")),
        )
        if not settings.vision_api_key or not settings.dashboard_api_key:
            raise RuntimeError("Set VISION_API_KEY and DASHBOARD_API_KEY (also required in demo)")
        if not demo and (not settings.supabase_url.startswith("https://") or not settings.supabase_secret_key):
            raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY, or explicitly enable local DEMO_MODE")
        if settings.cooldown_seconds < 0 or not 0 <= settings.min_confidence <= 1:
            raise RuntimeError("Invalid cooldown/confidence setting")
        return settings
