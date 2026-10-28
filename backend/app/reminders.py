"""WhatsApp payment reminders for customers with pattu outstanding.

Deterministic on purpose: the amount comes from the ledger, the wording from a fixed template. No model call,
so a reminder is free and can never state a wrong number. The shop owner sends it from their own WhatsApp.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from .config import settings
from .ledger import Ledger

# Don't nag: a second reminder within this window needs an explicit "yes, send again".
MIN_GAP = timedelta(days=3)


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Indian mobile -> '91XXXXXXXXXX'. Returns None for empty; raises ValueError for anything else."""
    if not raw or not raw.strip():
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10 and digits[0] in "6789":
        return "91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return digits
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        return "91" + digits[1:]
    raise ValueError("Enter a 10-digit Indian mobile number")


def _rupees(amount: float) -> str:
    """Indian grouping: ₹1,25,000 / ₹350.50"""
    rupees, paise = divmod(round(amount * 100), 100)
    s = str(rupees)
    if len(s) > 3:
        s = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3]) + "," + s[-3:]
    return "₹" + s + (f".{paise:02d}" if paise else "")


def message(name: str, amount: float) -> str:
    shop = settings.shop_name.strip()
    ml_shop = f"{shop} കടയിൽ" if shop else "നമ്മുടെ കടയിൽ"
    en_shop = f"at {shop}" if shop else "at our shop"
    sign = f"\n– {shop}" if shop else ""
    return (f"നമസ്കാരം {name}, {ml_shop} {_rupees(amount)} പറ്റ് ബാക്കിയുണ്ട്. "
            f"സൗകര്യം പോലെ തന്നു തീർക്കണേ. നന്ദി!\n\n"
            f"Hi {name}, your pending balance {en_shop} is {_rupees(amount)}. "
            f"Please clear it when convenient. Thank you!{sign}")


def whatsapp_url(phone: Optional[str], text: str) -> str:
    # Without a number WhatsApp opens the contact picker with the message ready.
    return f"https://wa.me/{phone or ''}?text={quote(text)}"


def _ago(iso: Optional[str], now: datetime) -> Optional[int]:
    if not iso:
        return None
    then = datetime.fromisoformat(iso)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).days


def due(ledger: Ledger, today_iso: str) -> list[dict]:
    """Everyone who owes money, most owed first, with when they last paid and were last reminded."""
    now = datetime.now(timezone.utc)
    today = datetime.fromisoformat(today_iso).date()
    out = []
    for c in ledger.customers():
        if c["balance"] <= 0:
            continue
        dates = ledger.last_dates(c["id"])
        last = ledger.last_reminder(c["id"])
        text = message(c["name"], c["balance"])
        days_since = lambda d: (today - datetime.fromisoformat(d).date()).days if d else None  # noqa: E731
        out.append({
            **c,
            "days_since_payment": days_since(dates["last_payment"]),
            "days_since_credit": days_since(dates["last_credit"]),
            "last_reminded_at": last["created_at"] if last else None,
            "days_since_reminder": _ago(last["created_at"], now) if last else None,
            "recently_reminded": bool(last) and now - datetime.fromisoformat(last["created_at"]) < MIN_GAP,
            "message": text,
            "whatsapp_url": whatsapp_url(c["phone"], text),
        })
    return sorted(out, key=lambda r: -r["balance"])
