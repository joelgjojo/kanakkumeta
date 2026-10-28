"""A small fictional shop for first-time visitors and judges. Only ever loaded into an empty ledger."""
from __future__ import annotations

from datetime import date, timedelta

from .ledger import Ledger

# (name, label, [(type, amount, days_ago, items)])
DEMO_SHOP = [
    ("Rajan", "auto driver", [("credit_sale", 500, 57, "rice, sugar")]),
    ("Rajan", "near church", [("credit_sale", 350, 3, "oil"), ("repayment", 100, 1, None)]),
    ("Joseph", None, [("credit_sale", 2400, 69, "rice, dal, soap"), ("repayment", 400, 48, None)]),
    ("Ammini", None, [("credit_sale", 120, 8, "coconut oil")]),
    ("Mary", None, [("credit_sale", 450, 2, "soap, biscuits")]),
    ("Suresh", None, [("credit_sale", 200, 20, "tea powder"), ("repayment", 200, 6, None)]),
]


def seed(ledger: Ledger, today: date) -> None:
    for name, label, txs in DEMO_SHOP:
        cid = ledger.create_customer(name, label)
        for tx_type, amount, days_ago, items in txs:
            ledger.add_transaction(customer_id=cid, tx_type=tx_type, amount=amount,
                                   tx_date=(today - timedelta(days=days_ago)).isoformat(), description=items)
