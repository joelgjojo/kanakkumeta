"""Append-only ledger. All money is stored as integer paise; balances are always SUMs, never a stored number.

There is deliberately NO function that sets a balance. The only way to change a balance is to append
a confirmed transaction (or a reversal), which leaves an audit trail.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HONORIFICS = {
    "chechi", "chechy", "chettan", "chetan", "chetta", "ikka", "ikkakka", "itha", "ammachi",
    "appachan", "uncle", "aunty", "auntie", "sir", "madam", "mam", "bhai", "anna", "akka",
    "ചേച്ചി", "ചേട്ടൻ", "ഇക്ക", "ഇത്ത",
}


def to_paise(amount: float) -> int:
    return int(round(amount * 100))


def from_paise(paise: int) -> float:
    return paise / 100


def customer_key(name: str) -> str:
    """Normalise a customer name for matching: lowercase, strip honorifics and punctuation."""
    words = re.findall(r"\w+", name.lower())
    words = [w for w in words if w not in HONORIFICS]
    return " ".join(words) if words else name.strip().lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    customer_key TEXT NOT NULL REFERENCES customers(key),
    type TEXT NOT NULL CHECK (type IN ('credit_sale', 'repayment', 'reversal')),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    tx_date TEXT NOT NULL,
    description TEXT,
    proposal_id TEXT,
    entry_index INTEGER,
    source_ref TEXT,
    acknowledged TEXT,
    reverses_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_ref TEXT,
    raw_input TEXT,
    model TEXT,
    model_output TEXT NOT NULL,
    proposed TEXT NOT NULL,
    entry_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- customers ----------
    def customers(self) -> list[dict]:
        rows = self.db.execute("SELECT key, display_name FROM customers ORDER BY display_name").fetchall()
        return [{"key": r["key"], "name": r["display_name"], "balance": self.balance(r["key"])} for r in rows]

    def find_customer(self, name: str) -> Optional[dict]:
        r = self.db.execute("SELECT key, display_name FROM customers WHERE key = ?", (customer_key(name),)).fetchone()
        return {"key": r["key"], "name": r["display_name"]} if r else None

    def customer_keys(self) -> dict[str, str]:
        return {r["key"]: r["display_name"] for r in self.db.execute("SELECT key, display_name FROM customers")}

    def _ensure_customer(self, name: str) -> str:
        key = customer_key(name)
        self.db.execute(
            "INSERT OR IGNORE INTO customers (key, display_name, created_at) VALUES (?, ?, ?)",
            (key, name.strip(), _now()),
        )
        return key

    # ---------- balances (always computed) ----------
    def balance_paise(self, key: str) -> int:
        row = self.db.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN t.type = 'credit_sale' THEN t.amount_paise END), 0)
            - COALESCE(SUM(CASE WHEN t.type = 'repayment'  THEN t.amount_paise END), 0)
            AS bal
            FROM transactions t
            WHERE t.customer_key = ? AND t.type != 'reversal'
              AND t.id NOT IN (SELECT reverses_id FROM transactions WHERE reverses_id IS NOT NULL)
            """,
            (key,),
        ).fetchone()
        return int(row["bal"])

    def balance(self, key: str) -> float:
        return from_paise(self.balance_paise(key))

    def total_outstanding(self) -> float:
        return sum(c["balance"] for c in self.customers() if c["balance"] > 0)

    # ---------- transactions ----------
    def add_transaction(
        self, *, customer_name: str, tx_type: str, amount: float, tx_date: str,
        description: Optional[str] = None, proposal_id: Optional[str] = None,
        entry_index: Optional[int] = None, source_ref: Optional[str] = None,
        acknowledged: Optional[list[str]] = None,
    ) -> dict:
        if tx_type not in ("credit_sale", "repayment"):
            raise ValueError(f"invalid transaction type: {tx_type}")
        paise = to_paise(amount)
        if paise <= 0:
            raise ValueError("amount must be positive")
        key = self._ensure_customer(customer_name)
        tx_id = "tx_" + uuid.uuid4().hex[:12]
        self.db.execute(
            """INSERT INTO transactions (id, customer_key, type, amount_paise, tx_date, description,
               proposal_id, entry_index, source_ref, acknowledged, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx_id, key, tx_type, paise, tx_date, description, proposal_id, entry_index, source_ref,
             json.dumps(acknowledged or []), _now()),
        )
        self.db.commit()
        return self.get_transaction(tx_id)

    def reverse_transaction(self, tx_id: str, reason: str = "") -> dict:
        """Undo by appending a reversal row, never by deleting."""
        t = self.get_transaction(tx_id)
        if not t:
            raise KeyError(tx_id)
        if t["type"] == "reversal" or t["reversed"]:
            raise ValueError("already reversed or is itself a reversal")
        rid = "tx_" + uuid.uuid4().hex[:12]
        self.db.execute(
            """INSERT INTO transactions (id, customer_key, type, amount_paise, tx_date, description,
               reverses_id, created_at) VALUES (?, ?, 'reversal', ?, ?, ?, ?, ?)""",
            (rid, t["customer_key"], to_paise(t["amount"]), t["date"], reason or "reversal", tx_id, _now()),
        )
        self.db.commit()
        return self.get_transaction(rid)

    def get_transaction(self, tx_id: str) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        return self._tx(r) if r else None

    def _tx(self, r: sqlite3.Row) -> dict:
        reversed_ = self.db.execute(
            "SELECT 1 FROM transactions WHERE reverses_id = ?", (r["id"],)
        ).fetchone() is not None
        return {
            "id": r["id"], "customer_key": r["customer_key"], "type": r["type"],
            "amount": from_paise(r["amount_paise"]), "date": r["tx_date"],
            "description": r["description"], "proposal_id": r["proposal_id"],
            "entry_index": r["entry_index"], "source_ref": r["source_ref"],
            "acknowledged": json.loads(r["acknowledged"] or "[]"), "reverses_id": r["reverses_id"],
            "reversed": reversed_, "created_at": r["created_at"],
        }

    def history(self, key: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM transactions WHERE customer_key = ? ORDER BY tx_date, created_at", (key,)
        ).fetchall()
        out, running = [], 0
        for r in rows:
            t = self._tx(r)
            if t["type"] != "reversal" and not t["reversed"]:
                running += to_paise(t["amount"]) * (1 if t["type"] == "credit_sale" else -1)
            t["running_balance"] = from_paise(running)
            out.append(t)
        return out

    def find_duplicates(self, key: str, tx_type: str, amount: float, tx_date: str) -> list[dict]:
        rows = self.db.execute(
            """SELECT * FROM transactions WHERE customer_key = ? AND type = ? AND amount_paise = ? AND tx_date = ?
               AND id NOT IN (SELECT reverses_id FROM transactions WHERE reverses_id IS NOT NULL)""",
            (key, tx_type, to_paise(amount), tx_date),
        ).fetchall()
        return [self._tx(r) for r in rows]

    def day_summary(self, day: str) -> dict:
        rows = self.db.execute(
            """SELECT type, COUNT(*) n, SUM(amount_paise) s FROM transactions
               WHERE tx_date = ? AND type != 'reversal'
               AND id NOT IN (SELECT reverses_id FROM transactions WHERE reverses_id IS NOT NULL)
               GROUP BY type""",
            (day,),
        ).fetchall()
        by = {r["type"]: {"count": r["n"], "total": from_paise(r["s"] or 0)} for r in rows}
        return {
            "date": day,
            "credit_given": by.get("credit_sale", {"count": 0, "total": 0.0}),
            "repayments": by.get("repayment", {"count": 0, "total": 0.0}),
            "total_outstanding": self.total_outstanding(),
        }

    # ---------- proposals ----------
    def save_proposal(self, *, pid: str, source_type: str, source_ref: Optional[str], raw_input: Optional[str],
                      model: str, model_output: dict, proposed: list[dict]) -> None:
        status = {str(p["index"]): "pending" for p in proposed}
        self.db.execute(
            """INSERT INTO proposals (id, source_type, source_ref, raw_input, model, model_output, proposed,
               entry_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, source_type, source_ref, raw_input, model, json.dumps(model_output),
             json.dumps(proposed), json.dumps(status), _now()),
        )
        self.db.commit()

    def get_proposal(self, pid: str) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
        if not r:
            return None
        return {
            "id": r["id"], "source_type": r["source_type"], "source_ref": r["source_ref"],
            "raw_input": r["raw_input"], "model": r["model"],
            "model_output": json.loads(r["model_output"]), "proposed": json.loads(r["proposed"]),
            "entry_status": json.loads(r["entry_status"]), "created_at": r["created_at"],
        }

    def set_entry_status(self, pid: str, index: int, status: str) -> None:
        p = self.get_proposal(pid)
        st = p["entry_status"]
        st[str(index)] = status
        self.db.execute("UPDATE proposals SET entry_status = ? WHERE id = ?", (json.dumps(st), pid))
        self.db.commit()
