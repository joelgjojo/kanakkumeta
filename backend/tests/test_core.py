"""Core money logic. These run with no API key and no network."""
from datetime import date

import pytest

from app.ledger import Ledger, customer_key
from app.schemas import Flag, ModelEntry, TxType
from app.validation import check_all, check_entry, plan_confirm

TODAY = date(2026, 10, 27)


@pytest.fixture
def L():
    return Ledger(":memory:")


def entry(**kw):
    return ModelEntry(**kw)


def cid(L, name):
    (c,) = L.find_customers(name)
    return c["id"]


def need(L, *, name, tx_type, amount, tx_date=TODAY, **kw):
    kw = {"customer_id": None, "new_customer": False, "label": None, "original_flags": [], **kw}
    return plan_confirm(ledger=L, name=name, tx_type=tx_type, amount=amount, tx_date=tx_date, today=TODAY, **kw).need


# ---------- ledger arithmetic ----------

def test_demo_b_balance(L):
    L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=350, tx_date="2026-10-27")
    L.add_transaction(customer_name="Rajan", tx_type="repayment", amount=200, tx_date="2026-10-27")
    assert L.balance(cid(L, "Rajan")) == 150


def test_paise_no_float_drift(L):
    for _ in range(10):
        L.add_transaction(customer_name="Mary", tx_type="credit_sale", amount=0.1, tx_date="2026-10-27")
    assert L.balance(cid(L, "Mary")) == 1.0


def test_reversal_is_append_only(L):
    t = L.add_transaction(customer_name="Ravi", tx_type="credit_sale", amount=500, tx_date="2026-10-27")
    L.reverse_transaction(t["id"], "entered twice")
    assert L.balance(cid(L, "Ravi")) == 0
    assert len(L.history(cid(L, "Ravi"))) == 2  # original + reversal both still visible
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
    assert L.find_customers("Ammini Chechi")[0]["name"] == "Ammini"


def test_running_balance_history(L):
    L.add_transaction(customer_name="Joseph", tx_type="credit_sale", amount=1000, tx_date="2026-10-20")
    L.add_transaction(customer_name="Joseph", tx_type="repayment", amount=400, tx_date="2026-10-25")
    h = L.history(cid(L, "Joseph"))
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
    assert need(L, name="Rajan", tx_type=TxType.credit_sale, amount=350) == {Flag.possible_duplicate}


def test_confirm_overpayment_matches_extraction(L):
    # A new customer's repayment is "ready" at extraction, so confirm must not suddenly demand an ack.
    assert need(L, name="Suresh", tx_type=TxType.repayment, amount=200) == set()
    L.add_transaction(customer_name="Anil", tx_type="credit_sale", amount=100, tx_date="2026-10-27")
    assert need(L, name="Anil", tx_type=TxType.repayment, amount=300) == {Flag.overpayment}


def test_confirm_rejects_future(L):
    with pytest.raises(ValueError):
        need(L, name="R", tx_type=TxType.credit_sale, amount=1, tx_date=date(2027, 1, 1))


# ---------- same name, different people ----------

def test_two_rajans_are_separate_accounts(L):
    a = L.create_customer("Rajan", label="auto driver")
    b = L.create_customer("Rajan", label="near church")
    L.add_transaction(customer_id=a, tx_type="credit_sale", amount=350, tx_date="2026-10-27")
    L.add_transaction(customer_id=b, tx_type="credit_sale", amount=90, tx_date="2026-10-27")
    assert (L.balance(a), L.balance(b)) == (350, 90)
    with pytest.raises(ValueError):  # a bare name can't silently pick one of them
        L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=1, tx_date="2026-10-27")


def test_ambiguous_name_blocks_and_lists_candidates(L):
    L.create_customer("Rajan", label="auto driver")
    L.create_customer("Rajan", label="near church")
    p = check_entry(entry(customer_name="Rajan chettan", transaction_type="credit_sale", amount=50),
                    index=0, ledger=L, today=TODAY, source_type="text")
    assert Flag.ambiguous_customer in p.flags and p.status == "needs_review"
    assert sorted(c["label"] for c in p.candidates) == ["auto driver", "near church"]
    assert p.matched_customer_id is None
    from app.ledger import AmbiguousCustomer
    with pytest.raises(AmbiguousCustomer):
        need(L, name="Rajan", tx_type=TxType.credit_sale, amount=50)


def test_second_account_with_same_name_needs_label_and_ack(L):
    L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=350, tx_date="2026-10-27")
    with pytest.raises(ValueError, match="tell the two apart"):
        need(L, name="Rajan", tx_type=TxType.credit_sale, amount=50, new_customer=True)
    assert need(L, name="Rajan", tx_type=TxType.credit_sale, amount=50, new_customer=True,
                label="teacher") == {Flag.same_name_customer}


def test_choosing_a_customer_checks_duplicates_on_that_account_only(L):
    a = L.create_customer("Rajan", label="auto driver")
    b = L.create_customer("Rajan", label="near church")
    L.add_transaction(customer_id=a, tx_type="credit_sale", amount=350, tx_date="2026-10-28")
    assert need(L, name="Rajan", customer_id=a, tx_type=TxType.credit_sale, amount=350,
                tx_date=date(2026, 10, 27)) == set()
    L.add_transaction(customer_id=a, tx_type="credit_sale", amount=350, tx_date="2026-10-27")
    assert need(L, name="Rajan", customer_id=a, tx_type=TxType.credit_sale, amount=350) == {Flag.possible_duplicate}
    assert need(L, name="Rajan", customer_id=b, tx_type=TxType.credit_sale, amount=350) == set()


def test_v1_database_is_migrated(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE customers (key TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE transactions (id TEXT PRIMARY KEY, customer_key TEXT NOT NULL, type TEXT NOT NULL,
            amount_paise INTEGER NOT NULL, tx_date TEXT NOT NULL, description TEXT, proposal_id TEXT,
            entry_index INTEGER, source_ref TEXT, acknowledged TEXT, reverses_id TEXT, created_at TEXT NOT NULL);
        INSERT INTO customers VALUES ('rajan', 'Rajan', '2026-10-27T00:00:00+00:00');
        INSERT INTO transactions VALUES ('tx_1', 'rajan', 'credit_sale', 35000, '2026-10-27', NULL, NULL, NULL,
            NULL, '[]', NULL, '2026-10-27T00:00:00+00:00');
    """)
    con.close()
    L2 = Ledger(db)
    (c,) = L2.customers()
    assert c["name"] == "Rajan" and c["balance"] == 350 and c["id"].startswith("c_")


# ---------- reminders ----------

def test_reminder_text_uses_ledger_amount_and_indian_grouping():
    from app import reminders
    m = reminders.message("Rajan", 125000.5)
    assert "₹1,25,000.50" in m and "Rajan" in m
    assert reminders.normalize_phone("98470 12345") == "919847012345"
    assert reminders.normalize_phone("+91 98470-12345") == "919847012345"
    assert reminders.normalize_phone("") is None
    with pytest.raises(ValueError):
        reminders.normalize_phone("12345")


def test_due_list_only_people_who_owe(L):
    from app import reminders
    L.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=350, tx_date="2026-10-20")
    L.add_transaction(customer_name="Mary", tx_type="credit_sale", amount=50, tx_date="2026-10-20")
    L.add_transaction(customer_name="Mary", tx_type="repayment", amount=50, tx_date="2026-10-25")
    due = reminders.due(L, "2026-10-27")
    assert [d["name"] for d in due] == ["Rajan"]
    assert due[0]["days_since_credit"] == 7 and due[0]["days_since_payment"] is None
    assert due[0]["whatsapp_url"].startswith("https://wa.me/?text=")


# ---------- voice ----------

def _wav(rate=16000, channels=1, seconds=1):
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * channels * rate * seconds)
    return buf.getvalue()


def test_wav_format_is_checked_before_any_api_call():
    from app.extractor import ExtractionError, check_wav
    assert check_wav(_wav()) == 1000
    with pytest.raises(ExtractionError, match="mono"):
        check_wav(_wav(rate=44100))
    with pytest.raises(ExtractionError, match="mono"):
        check_wav(_wav(channels=2))
    with pytest.raises(ExtractionError, match="Not a WAV"):
        check_wav(b"ID3 this is an mp3")


def test_voice_entries_default_to_today(L):
    p = check_entry(entry(customer_name="Rajan", transaction_type="credit_sale", amount=350),
                    index=0, ledger=L, today=TODAY, source_type="voice")
    assert p.transaction_date == "2026-10-27" and p.status == "ready"
