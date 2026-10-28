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
    # minimal | low | medium | high | xhigh, or empty for the API default. On the 25-case eval "minimal" scored
    # the same as the default at half the cost and latency, so typed and voice input use it.
    reasoning_effort: str = os.getenv("REASONING_EFFORT", "minimal")
    # Handwriting is harder than typed text; photos keep the API default unless measured otherwise.
    image_reasoning_effort: str = os.getenv("IMAGE_REASONING_EFFORT", "")
    timezone: str = os.getenv("SHOP_TIMEZONE", "Asia/Kolkata")
    data_dir: Path = Path(os.getenv("DATA_DIR", str(BACKEND_DIR / "data")))
    # Cache real model responses on disk keyed by (model, prompt, input). Saves credits while building the UI.
    cache: bool = os.getenv("KANAKKU_CACHE", "1") == "1"
    # USD per 1M tokens, for cost logging only. Check your Meta dashboard and update.
    price_in: float = float(os.getenv("PRICE_IN_PER_M", "1.25"))
    price_out: float = float(os.getenv("PRICE_OUT_PER_M", "4.25"))
    asr_model: str = os.getenv("ASR_MODEL", "muse-voice-transcribe-1.0")
    asr_price_per_hour: float = float(os.getenv("ASR_PRICE_PER_HOUR", "0.18"))
    # Shown in WhatsApp reminders. Empty = "our shop".
    shop_name: str = os.getenv("SHOP_NAME", "")
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")


settings = Settings()
