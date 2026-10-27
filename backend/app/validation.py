"""Deterministic checks between the model and the ledger.

The model PROPOSES. This module decides what needs a human. Nothing here calls the model.
"""
from __future__ import annotations

import difflib
import re
from datetime import date, timedelta
from typing import Optional

from .ledger import Ledger, customer_key
from .schemas import HARD_FLAGS, SOFT_FLAGS, Flag, ModelEntry, ProposedEntry, TxType

MAX_AMOUNT = 10_000_000  # ₹1 crore: anything bigger in a kirana notebook is almost certainly a misread

MESSAGES = {
    Flag.unclear_direction: "Can't tell if this is credit given or money received. Please choose.",
    Flag.missing_customer: "No customer name found.",
    Flag.missing_amount: "No amount found.",
    Flag.uncertain_amount: "The amount isn't clearly written. Please check it.",
    Flag.illegible: "Part of this entry is hard to read.",
    Flag.missing_date: "No date found for this entry.",
    Flag.invalid_amount: "That amount isn't valid (zero, negative or too large).",
    Flag.amount_from_balance: "'Paid in full' — amount filled from the current balance. Please confirm.",
    Flag.possible_duplicate: "An identical entry already exists for this day.",
    Flag.similar_customer: "This name looks like an existing customer. Same person?",
    Flag.new_customer: "New customer — will be added to the ledger.",
    Flag.overpayment: "This repayment is more than what the customer owes.",
    Flag.invalid_date: "That date doesn't look right.",
}


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _looks_negative(amount_text: Optional[str]) -> bool:
    return bool(amount_text) and bool(re.search(r"(^|\s)[-−–]\s*\d", amount_text))


def check_entry(
    e: ModelEntry, *, index: int, ledger: Ledger, today: date, source_type: str,
    batch: list[ProposedEntry] | None = None,
) -> ProposedEntry:
    flags: list[Flag] = list(dict.fromkeys(e.flags))  # model flags first, deduped
    name = (e.customer_name or "").strip() or None
    tx_type = e.transaction_type
    amount = e.amount
    matched = similar = dup_of = None
    current_balance = None

    # --- customer ---
    if not name:
        flags.append(Flag.missing_customer)
    else:
        existing = ledger.find_customer(name)
        if existing:
            matched = existing["name"]
            current_balance = ledger.balance(existing["key"])
        else:
            keys = ledger.customer_keys()
            close = difflib.get_close_matches(customer_key(name), list(keys), n=1, cutoff=0.8)
            if close:
                similar = keys[close[0]]
                flags.append(Flag.similar_customer)
            else:
                flags.append(Flag.new_customer)

    # --- direction ---
    if tx_type is None:
        flags.append(Flag.unclear_direction)

    # --- amount ---
    if _looks_negative(e.amount_text) or (amount is not None and (amount <= 0 or amount > MAX_AMOUNT)):
        flags.append(Flag.invalid_amount)
        amount = None
    if amount is not None and round(amount, 2) != amount:
        amount = round(amount, 2)
    if amount is None and e.paid_in_full and tx_type == TxType.repayment and current_balance and current_balance > 0:
        # The model must never invent this number. Code reads it from the ledger.
        amount = current_balance
        flags.append(Flag.amount_from_balance)
    if amount is None and Flag.invalid_amount not in flags and Flag.uncertain_amount not in flags:
        flags.append(Flag.missing_amount)
    if amount is None and Flag.uncertain_amount in flags:
        flags.append(Flag.missing_amount)

    # --- date ---
    d = _parse_date(e.transaction_date)
    if e.transaction_date and d is None:
        flags.append(Flag.invalid_date)
    elif d is None:
        if source_type == "text":
            d = today  # typed messages default to today: that's how shopkeepers use it
        elif Flag.missing_date not in flags:
            flags.append(Flag.missing_date)
    if d is not None and (d > today or d < today - timedelta(days=366 * 3)):
        flags.append(Flag.invalid_date)
    tx_date = d.isoformat() if d else None

    # --- business rules ---
    if tx_type == TxType.repayment and amount and current_balance is not None and amount > current_balance + 0.001:
        flags.append(Flag.overpayment)
    if name and tx_type and amount and tx_date:
        dups = ledger.find_duplicates(customer_key(name), tx_type.value, amount, tx_date)
        if dups:
            dup_of = dups[0]["id"]
            flags.append(Flag.possible_duplicate)
        elif batch:
            for other in batch:
                if (other.customer_name and customer_key(other.customer_name) == customer_key(name)
                        and other.transaction_type == tx_type and other.amount == amount
                        and other.transaction_date == tx_date):
                    dup_of = f"entry #{other.index + 1} in this upload"
                    flags.append(Flag.possible_duplicate)
                    break

    flags = list(dict.fromkeys(flags))
    if Flag.missing_amount in flags and Flag.amount_from_balance in flags:
        flags.remove(Flag.missing_amount)
    blocking = [f for f in flags if f in HARD_FLAGS or f in SOFT_FLAGS]
    return ProposedEntry(
        index=index, customer_name=name, transaction_type=tx_type, amount=amount, transaction_date=tx_date,
        description=e.description, evidence=e.evidence, box=e.box, flags=flags,
        flag_messages={f.value: MESSAGES[f] for f in flags},
        matched_customer=matched, similar_to=similar, duplicate_of=dup_of, current_balance=current_balance,
        status="needs_review" if blocking else "ready",
    )


def check_all(entries: list[ModelEntry], *, ledger: Ledger, today: date, source_type: str) -> list[ProposedEntry]:
    out: list[ProposedEntry] = []
    for i, e in enumerate(entries):
        out.append(check_entry(e, index=i, ledger=ledger, today=today, source_type=source_type, batch=out))
    return out


def soft_flags_for_confirm(
    *, ledger: Ledger, name: str, tx_type: TxType, amount: float, tx_date: date, today: date,
    original_flags: list[str],
) -> set[Flag]:
    """Re-run the checks on the FINAL values the user submitted. Returns soft flags that must be acknowledged.

    Raises ValueError for anything that can never be saved.
    """
    if amount <= 0 or amount > MAX_AMOUNT:
        raise ValueError("invalid amount")
    if tx_date > today:
        raise ValueError("date is in the future")
    need: set[Flag] = set()
    key = customer_key(name)
    existing = ledger.find_customer(name)
    if not existing:
        keys = ledger.customer_keys()
        if difflib.get_close_matches(key, list(keys), n=1, cutoff=0.8):
            need.add(Flag.similar_customer)
    if ledger.find_duplicates(key, tx_type.value, amount, tx_date.isoformat()):
        need.add(Flag.possible_duplicate)
    # Same rule as check_entry: a new customer repaying is usually an old paper-book debt, not an overpayment.
    if tx_type == TxType.repayment and existing and amount > ledger.balance(existing["key"]) + 0.001:
        need.add(Flag.overpayment)
    # If the model was unsure about the amount, the human must say "yes, I checked it".
    for f in (Flag.uncertain_amount, Flag.amount_from_balance):
        if f.value in original_flags:
            need.add(f)
    return need
