"""Step 1 tonight: prove the key works and see which models you have.

    python scripts/ping.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app.config import settings  # noqa: E402

if not settings.api_key:
    sys.exit("Set MODEL_API_KEY in backend/.env first.")

client = OpenAI(base_url=settings.base_url, api_key=settings.api_key)

print("Models available to this key:")
try:
    for m in client.models.list():
        print("  -", m.id)
except Exception as e:
    print("  (could not list models:", e, ")")

print(f"\nCalling {settings.model} ...")
r = client.chat.completions.create(
    model=settings.model,
    messages=[{"role": "user", "content": "Reply with exactly: Kanakku is alive"}],
)
print("Reply:", r.choices[0].message.content)
print("Usage:", r.usage)
if r.usage:
    cost = r.usage.prompt_tokens / 1e6 * settings.price_in + r.usage.completion_tokens / 1e6 * settings.price_out
    print(f"Est. cost: ${cost:.6f}  (check prices on your Meta dashboard and set PRICE_IN_PER_M / PRICE_OUT_PER_M)")
