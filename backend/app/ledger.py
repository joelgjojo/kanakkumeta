"""Append-only ledger. All money is stored as integer paise; balances are always SUMs, never a stored number.

There is deliberately NO function that sets a balance. The only way to change a balance is to append
a confirmed transaction (or a reversal), which leaves an audit trail.

Customers are identified by id, not by name: a village shop can have three Rajans. The normalised name
(`key`) is only used to FIND candidates; a human picks which one.
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


class AmbiguousCustomer(ValueError):
    def __init__(self, name: str, candidates: list[dict]):
        super().__init__(f"{len(candidates)} customers are called {name}; choose one")
        self.candidates = candidates


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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,              -- normalised name, NOT unique
    display_name TEXT NOT NULL,
    label TEXT,                     -- what tells two Rajans apart: "auto driver", "near church"
    phone TEXT,                     -- digits with country code, for WhatsApp reminders
    overdue_days INTEGER,           -- this customer's own payment cycle; NULL = the shop's default
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS customers_by_key ON customers(key);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
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
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    amount_paise INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# v1 keyed customers by normalised name, which merged different people with the same name.
MIGRATE_V1 = """
ALTER TABLE customers RENAME TO customers_v1;
ALTER TABLE transactions RENAME TO transactions_v1;
{schema}
INSERT INTO customers (id, key, display_name, created_at)
    SELECT 'c_' || lower(hex(randomblob(6))), key, display_name, created_at FROM customers_v1;
INSERT INTO transactions (id, customer_id, type, amount_paise, tx_date, description, proposal_id, entry_index,
                          source_ref, acknowledged, reverses_id, created_at)
    SELECT t.id, c.id, t.type, t.amount_paise, t.tx_date, t.description, t.proposal_id, t.entry_index,
           t.source_ref, t.acknowledged, t.reverses_id, t.created_at
    FROM transactions_v1 t JOIN customers c ON c.key = t.customer_key;
DROP TABLE transactions_v1;
DROP TABLE customers_v1;
"""

NOT_REVERSED = "id NOT IN (SELECT reverses_id FROM transactions WHERE reverses_id IS NOT NULL)"


class Ledger:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(customers)")}
        if cols and "id" not in cols:
            self.db.executescript(MIGRATE_V1.format(schema=SCHEMA))
        elif cols and "overdue_days" not in cols:
            self.db.execute("ALTER TABLE customers ADD COLUMN overdue_days INTEGER")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- customers ----------
    def _customer(self, r: sqlite3.Row) -> dict:
        return {"id": r["id"], "name": r["display_name"], "label": r["label"], "phone": r["phone"],
                "overdue_days": r["overdue_days"], "balance": self.balance(r["id"])}

    def customers(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM customers ORDER BY display_name, label").fetchall()
        return [self._customer(r) for r in rows]

    def get_customer(self, cid: str) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
        return self._customer(r) if r else None

    def find_customers(self, name: str) -> list[dict]:
        """Every customer whose normalised name matches. More than one means a human must choose."""
        rows = self.db.execute("SELECT * FROM customers WHERE key = ? ORDER BY created_at",
                               (customer_key(name),)).fetchall()
        return [self._customer(r) for r in rows]

    def customer_keys(self) -> dict[str, str]:
        return {r["key"]: r["display_name"] for r in self.db.execute("SELECT key, display_name FROM customers")}

    def create_customer(self, name: str, label: Optional[str] = None, phone: Optional[str] = None) -> str:
        cid = _new_id("c")
        self.db.execute(
            "INSERT INTO customers (id, key, display_name, label, phone, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cid, customer_key(name), name.strip(), (label or "").strip() or None, phone, _now()),
        )
        self.db.commit()
        return cid

    def update_customer(self, cid: str, *, label: Optional[str] = None, phone: Optional[str] = None,
                        overdue_days: Optional[int] = None) -> dict:
        """Contact details and payment cycle only. Money fields can't be edited here or anywhere else.

        overdue_days: None = unchanged, 0 = back to the shop default.
        """
        if label is not None:
            self.db.execute("UPDATE customers SET label = ? WHERE id = ?", (label.strip() or None, cid))
        if phone is not None:
            self.db.execute("UPDATE customers SET phone = ? WHERE id = ?", (phone or None, cid))
        if overdue_days is not None:
            self.db.execute("UPDATE customers SET overdue_days = ? WHERE id = ?", (overdue_days or None, cid))
        self.db.commit()
        return self.get_customer(cid)

    # ---------- balances (always computed) ----------
    def balance_paise(self, cid: str) -> int:
        row = self.db.execute(
            f"""
            SELECT
              COALESCE(SUM(CASE WHEN type = 'credit_sale' THEN amount_paise END), 0)
            - COALESCE(SUM(CASE WHEN type = 'repayment'  THEN amount_paise END), 0)
            AS bal
            FROM transactions
            WHERE customer_id = ? AND type != 'reversal' AND {NOT_REVERSED}
            """,
            (cid,),
        ).fetchone()
        return int(row["bal"])

    def balance(self, cid: str) -> float:
        return from_paise(self.balance_paise(cid))

    def total_outstanding(self) -> float:
        return sum(c["balance"] for c in self.customers() if c["balance"] > 0)

    def owing_since(self, cid: str) -> Optional[str]:
        """Date the current debt started: when the running balance last went from settled to owing."""
        since = None
        for t in self.history(cid):
            if t["type"] == "reversal" or t["reversed"]:
                continue
            if t["running_balance"] <= 0:
                since = None
            elif since is None:
                since = t["date"]
        return since

    def last_dates(self, cid: str) -> dict:
        rows = self.db.execute(
            f"""SELECT type, MAX(tx_date) d FROM transactions
                WHERE customer_id = ? AND type != 'reversal' AND {NOT_REVERSED} GROUP BY type""",
            (cid,),
        ).fetchall()
        by = {r["type"]: r["d"] for r in rows}
        return {"last_credit": by.get("credit_sale"), "last_payment": by.get("repayment")}

    # ---------- transactions ----------
    def add_transaction(
        self, *, tx_type: str, amount: float, tx_date: str, customer_id: Optional[str] = None,
        customer_name: Optional[str] = None, description: Optional[str] = None,
        proposal_id: Optional[str] = None, entry_index: Optional[int] = None,
        source_ref: Optional[str] = None, acknowledged: Optional[list[str]] = None,
    ) -> dict:
        if tx_type not in ("credit_sale", "repayment"):
            raise ValueError(f"invalid transaction type: {tx_type}")
        paise = to_paise(amount)
        if paise <= 0:
            raise ValueError("amount must be positive")
        if customer_id is None:
            if not customer_name:
                raise ValueError("customer_id or customer_name is required")
            # Convenience for seeds/tests: a name is only accepted when it can't be confused with anyone.
            matches = self.find_customers(customer_name)
            if len(matches) > 1:
                raise AmbiguousCustomer(customer_name, matches)
            customer_id = matches[0]["id"] if matches else self.create_customer(customer_name)
        elif not self.get_customer(customer_id):
            raise ValueError(f"unknown customer {customer_id}")
        tx_id = _new_id("tx")
        self.db.execute(
            """INSERT INTO transactions (id, customer_id, type, amount_paise, tx_date, description,
               proposal_id, entry_index, source_ref, acknowledged, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx_id, customer_id, tx_type, paise, tx_date, description, proposal_id, entry_index, source_ref,
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
        rid = _new_id("tx")
        self.db.execute(
            """INSERT INTO transactions (id, customer_id, type, amount_paise, tx_date, description,
               reverses_id, created_at) VALUES (?, ?, 'reversal', ?, ?, ?, ?, ?)""",
            (rid, t["customer_id"], to_paise(t["amount"]), t["date"], reason or "reversal", tx_id, _now()),
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
            "id": r["id"], "customer_id": r["customer_id"], "type": r["type"],
            "amount": from_paise(r["amount_paise"]), "date": r["tx_date"],
            "description": r["description"], "proposal_id": r["proposal_id"],
            "entry_index": r["entry_index"], "source_ref": r["source_ref"],
            "acknowledged": json.loads(r["acknowledged"] or "[]"), "reverses_id": r["reverses_id"],
            "reversed": reversed_, "created_at": r["created_at"],
        }

    def history(self, cid: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM transactions WHERE customer_id = ? ORDER BY tx_date, created_at", (cid,)
        ).fetchall()
        out, running = [], 0
        for r in rows:
            t = self._tx(r)
            if t["type"] != "reversal" and not t["reversed"]:
                running += to_paise(t["amount"]) * (1 if t["type"] == "credit_sale" else -1)
            t["running_balance"] = from_paise(running)
            out.append(t)
        return out

    def find_duplicates(self, cid: str, tx_type: str, amount: float, tx_date: str) -> list[dict]:
        rows = self.db.execute(
            f"""SELECT * FROM transactions WHERE customer_id = ? AND type = ? AND amount_paise = ? AND tx_date = ?
               AND {NOT_REVERSED}""",
            (cid, tx_type, to_paise(amount), tx_date),
        ).fetchall()
        return [self._tx(r) for r in rows]

    def day_summary(self, day: str) -> dict:
        rows = self.db.execute(
            f"""SELECT type, COUNT(*) n, SUM(amount_paise) s FROM transactions
               WHERE tx_date = ? AND type != 'reversal' AND {NOT_REVERSED}
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

    # ---------- reminders ----------
    def log_reminder(self, cid: str, amount: float, message: str) -> dict:
        rid = _new_id("r")
        now = _now()
        self.db.execute("INSERT INTO reminders (id, customer_id, amount_paise, message, created_at) VALUES (?, ?, ?, ?, ?)",
                        (rid, cid, to_paise(amount), message, now))
        self.db.commit()
        return {"id": rid, "customer_id": cid, "amount": amount, "created_at": now}

    def last_reminder(self, cid: str) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM reminders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 1",
                            (cid,)).fetchone()
        return {"amount": from_paise(r["amount_paise"]), "created_at": r["created_at"]} if r else None

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
