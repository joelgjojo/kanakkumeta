import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    api_key: str = os.getenv("MODEL_API_KEY", "")
    base_url: str = os.getenv("MODEL_API_BASE", "https://api.meta.ai/v1")
    model: str = os.getenv("MODEL_NAME", "muse-spark-1.3")
    # Leave empty to let the API use its default. Try "low" for text to save credits.
    reasoning_effort: str = os.getenv("REASONING_EFFORT", "")
    timezone: str = os.getenv("SHOP_TIMEZONE", "Asia/Kolkata")
    data_dir: Path = Path(os.getenv("DATA_DIR", str(BACKEND_DIR / "data")))
    # Cache real model responses on disk keyed by (model, prompt, input). Saves credits while building the UI.
    cache: bool = os.getenv("KANAKKU_CACHE", "1") == "1"
    # USD per 1M tokens, for cost logging only. Check your Meta dashboard and update.
    price_in: float = float(os.getenv("PRICE_IN_PER_M", "1.25"))
    price_out: float = float(os.getenv("PRICE_OUT_PER_M", "4.25"))
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")


settings = Settings()
