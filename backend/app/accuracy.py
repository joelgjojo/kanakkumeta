"""How accurate is the AI for THIS shop? Measured from the shopkeeper's own corrections, not claimed.

Every saved entry has the model's proposal next to it. A field the shopkeeper changed before saving is a model
error; a row that was wrong AND had been flagged "needs your check" is an error the system caught itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .ledger import Ledger, customer_key, to_paise

FIELDS = ("customer_name", "transaction_type", "amount", "transaction_date")


def _right(field: str, proposed: dict, saved: dict) -> bool:
    if field == "customer_name":
        return customer_key(proposed.get("customer_name") or "") == saved["customer_key"]
    if field == "transaction_type":
        return proposed.get("transaction_type") == saved["type"]
    if field == "amount":
        return proposed.get("amount") is not None and to_paise(proposed["amount"]) == saved["amount_paise"]
    return proposed.get("transaction_date") == saved["tx_date"]


def report(ledger: Ledger, days: int = 30) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    saved = {(r["proposal_id"], r["entry_index"]): dict(r) for r in ledger.db.execute(
        """SELECT t.proposal_id, t.entry_index, t.type, t.amount_paise, t.tx_date, c.key AS customer_key
           FROM transactions t JOIN customers c ON c.id = t.customer_id
           WHERE t.proposal_id IS NOT NULL AND t.type != 'reversal'""")}
    stats: dict[str, dict] = {}
    for p in ledger.db.execute("SELECT id, source_type, proposed, entry_status FROM proposals WHERE created_at >= ?",
                               (since,)):
        s = stats.setdefault(p["source_type"], {"saved": 0, "skipped": 0, "fields": 0, "fields_right": 0,
                                                "rows_unchanged": 0, "rows_corrected": 0, "corrected_flagged": 0})
        status = json.loads(p["entry_status"])
        for e in json.loads(p["proposed"]):
            st = status.get(str(e["index"]))
            if st == "skipped":
                s["skipped"] += 1
            t = saved.get((p["id"], e["index"]))
            if st != "confirmed" or not t:
                continue
            right = [_right(f, e, t) for f in FIELDS]
            s["saved"] += 1
            s["fields"] += len(FIELDS)
            s["fields_right"] += sum(right)
            if all(right):
                s["rows_unchanged"] += 1
            else:
                s["rows_corrected"] += 1
                s["corrected_flagged"] += e.get("status") == "needs_review"

    def ratio(a, b):
        return round(a / b, 3) if b else None

    out = {}
    for src, s in stats.items():
        out[src] = {**s,
                    "fields_right_first_time": ratio(s["fields_right"], s["fields"]),
                    "rows_unchanged_rate": ratio(s["rows_unchanged"], s["saved"]),
                    # Of the rows the model got wrong, how many had it already marked "needs your check"?
                    "errors_flagged_rate": ratio(s["corrected_flagged"], s["rows_corrected"])}
    return {"days": days, "by_source": out}
