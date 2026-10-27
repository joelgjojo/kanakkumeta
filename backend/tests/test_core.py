"""Core money logic. These run with no API key and no network."""
from datetime import date

import pytest

from app.ledger import Ledger, customer_key
from app.schemas import Flag, ModelEntry, TxType
from app.validation import check_all, check_entry, soft_flags_for_confirm

TODAY = date(2026, 10, 27)


@pytest.fixture
def L():
    return Ledger(":memory:")


def entry(**kw):
    return ModelEntry(**kw)


# ---------- ledger arithmetic ----------

def test_demo_b_balance(L):
    L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=350, tx_date="2026-10-27")
    L.add_transaction(customer_name="Rajan", tx_type="repayment", amount=200, tx_date="2026-10-27")
    assert L.balance(customer_key("Rajan")) == 150


def test_paise_no_float_drift(L):
    for _ in range(10):
        L.add_transaction(customer_name="Mary", tx_type="credit_sale", amount=0.1, tx_date="2026-10-27")
    assert L.balance("mary") == 1.0


def test_reversal_is_append_only(L):
    t = L.add_transaction(customer_name="Ravi", tx_type="credit_sale", amount=500, tx_date="2026-10-27")
    L.reverse_transaction(t["id"], "entered twice")
    assert L.balance("ravi") == 0
    assert len(L.history("ravi")) == 2  # original + reversal both still visible
    with pytest.raises(ValueError):
        L.reverse_transaction(t["id"])


def test_rejects_bad_amounts(L):
    with pytest.raises(ValueError):
        L.add_transaction(customer_name="X", tx_type="credit_sale", amount=0, tx_date="2026-10-27")
    with pytest.raises(ValueError):
        L.add_transaction(customer_name="X", tx_type="credit_sale", amount=-5, tx_date="2026-10-27")
    with pytest.raises(ValueError):
        L.add_transaction(customer_name="X", tx_type="set_balance", amount=5, tx_date="2026-10-27")


def test_honorifics_map_to_same_customer(L):
    L.add_transaction(customer_name="Ammini", tx_type="credit_sale", amount=120, tx_date="2026-10-27")
    assert customer_key("Ammini chechi") == customer_key("ammini")
    assert L.find_customer("Ammini Chechi")["name"] == "Ammini"


def test_running_balance_history(L):
    L.add_transaction(customer_name="Joseph", tx_type="credit_sale", amount=1000, tx_date="2026-10-20")
    L.add_transaction(customer_name="Joseph", tx_type="repayment", amount=400, tx_date="2026-10-25")
    h = L.history("joseph")
    assert [t["running_balance"] for t in h] == [1000, 600]


# ---------- validation: clean entries ----------

def test_clean_text_entry_is_ready_but_not_saved(L):
    p = check_entry(entry(customer_name="Rajan", transaction_type="credit_sale", amount=350),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert p.status == "ready"
    assert p.transaction_date == "2026-10-27"   # text defaults to today
    assert Flag.new_customer in p.flags          # info only
    assert L.customers() == []                   # validation never writes


# ---------- validation: the "ask, don't guess" cases ----------

def test_unclear_direction_blocks(L):
    p = check_entry(entry(customer_name="Latha", amount=300), index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.unclear_direction in p.flags and p.status == "needs_review"


def test_missing_name_and_amount(L):
    p = check_entry(entry(transaction_type="credit_sale", amount=450), index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.missing_customer in p.flags
    p = check_entry(entry(customer_name="Vinod", transaction_type="credit_sale"), index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.missing_amount in p.flags


def test_negative_amount_rejected_even_if_model_returns_positive(L):
    p = check_entry(entry(customer_name="Rajan", transaction_type="credit_sale", amount=350, amount_text="-350"),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.invalid_amount in p.flags and p.amount is None


def test_paid_in_full_amount_comes_from_ledger_not_model(L):
    L.add_transaction(customer_name="Sini", tx_type="credit_sale", amount=640, tx_date="2026-10-20")
    p = check_entry(entry(customer_name="Sini", transaction_type="repayment", paid_in_full=True),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert p.amount == 640
    assert Flag.amount_from_balance in p.flags and p.status == "needs_review"


def test_duplicate_against_ledger(L):
    L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=350, tx_date="2026-10-27")
    p = check_entry(entry(customer_name="Rajan", transaction_type="credit_sale", amount=350),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.possible_duplicate in p.flags and p.duplicate_of


def test_duplicate_within_one_photo(L):
    ps = check_all([
        entry(customer_name="Ravi", transaction_type="credit_sale", amount=90, transaction_date="2026-10-25"),
        entry(customer_name="Ravi", transaction_type="credit_sale", amount=90, transaction_date="2026-10-25"),
    ], ledger=L, today=TODAY, source_type="image")
    assert Flag.possible_duplicate not in ps[0].flags
    assert Flag.possible_duplicate in ps[1].flags


def test_similar_name_asks(L):
    L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=10, tx_date="2026-10-27")
    p = check_entry(entry(customer_name="Rajen", transaction_type="credit_sale", amount=50),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.similar_customer in p.flags and p.similar_to == "Rajan"


def test_overpayment_flagged(L):
    L.add_transaction(customer_name="Anil", tx_type="credit_sale", amount=100, tx_date="2026-10-27")
    p = check_entry(entry(customer_name="Anil", transaction_type="repayment", amount=300),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.overpayment in p.flags


def test_future_date_and_image_missing_date(L):
    p = check_entry(entry(customer_name="A", transaction_type="credit_sale", amount=5, transaction_date="2026-12-01"),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.invalid_date in p.flags
    p = check_entry(entry(customer_name="A", transaction_type="credit_sale", amount=5),
                    index=0, ledger=L, today=TODAY, source_type="image")
    assert Flag.missing_date in p.flags


def test_model_uncertainty_is_kept(L):
    p = check_entry(entry(customer_name="Vinod", transaction_type="credit_sale", amount=200, amount_text="2oo",
                          flags=["uncertain_amount"]), index=0, ledger=L, today=TODAY, source_type="text")
    assert p.amount == 200 and p.status == "needs_review"


# ---------- confirm-time re-checks ----------

def test_confirm_requires_ack_for_duplicate(L):
    L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=350, tx_date="2026-10-27")
    need = soft_flags_for_confirm(ledger=L, name="Rajan", tx_type=TxType.credit_sale, amount=350,
                                  tx_date=TODAY, today=TODAY, original_flags=[])
    assert need == {Flag.possible_duplicate}


def test_confirm_overpayment_matches_extraction(L):
    # A new customer's repayment is "ready" at extraction, so confirm must not suddenly demand an ack.
    assert soft_flags_for_confirm(ledger=L, name="Suresh", tx_type=TxType.repayment, amount=200,
                                  tx_date=TODAY, today=TODAY, original_flags=[]) == set()
    L.add_transaction(customer_name="Anil", tx_type="credit_sale", amount=100, tx_date="2026-10-27")
    assert soft_flags_for_confirm(ledger=L, name="Anil", tx_type=TxType.repayment, amount=300,
                                  tx_date=TODAY, today=TODAY, original_flags=[]) == {Flag.overpayment}


def test_confirm_rejects_future(L):
    with pytest.raises(ValueError):
        soft_flags_for_confirm(ledger=L, name="R", tx_type=TxType.credit_sale, amount=1,
                               tx_date=date(2027, 1, 1), today=TODAY, original_flags=[])
