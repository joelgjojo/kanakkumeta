"""End-to-end API flow with the model stubbed out (tests the app, not the model)."""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["MODEL_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import extractor, main  # noqa: E402
from app.schemas import ModelOutput  # noqa: E402

client = TestClient(main.app)


def stub(output: dict):
    def fake(*args, **kwargs):
        return extractor.Extraction(ModelOutput.model_validate(output), output, "stub", False, None, 0)
    return fake


def E(**kw):
    base = dict(customer_name=None, transaction_type=None, amount=None, amount_text=None,
                transaction_date=None, description=None, paid_in_full=False, flags=[], evidence=None, box=None)
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def fresh_ledger(monkeypatch):
    from app.ledger import Ledger
    monkeypatch.setattr(main, "ledger", Ledger(":memory:"))


def test_demo_b_full_flow(monkeypatch):
    monkeypatch.setattr(extractor, "extract_text", stub({"intent": "transactions", "entries": [
        E(customer_name="Rajan", transaction_type="credit_sale", amount=350, amount_text="350")],
        "query_customer": None, "note": None}))
    r = client.post("/api/extract/text", json={"text": "Rajan 350 pattu"}).json()
    e = r["entries"][0]
    assert e["status"] == "ready"
    assert client.get("/api/customers").json() == []  # extraction alone saves nothing

    c = client.post(f"/api/proposals/{r['proposal_id']}/confirm", json={
        "index": 0, "customer_name": "Rajan", "transaction_type": "credit_sale", "amount": 350,
        "transaction_date": e["transaction_date"]}).json()
    assert c["balance"] == 350

    monkeypatch.setattr(extractor, "extract_text", stub({"intent": "transactions", "entries": [
        E(customer_name="Rajan", transaction_type="repayment", amount=200, amount_text="200")],
        "query_customer": None, "note": None}))
    r = client.post("/api/extract/text", json={"text": "Rajan paid 200 today"}).json()
    c = client.post(f"/api/proposals/{r['proposal_id']}/confirm", json={
        "index": 0, "customer_name": "Rajan", "transaction_type": "repayment", "amount": 200,
        "transaction_date": r["entries"][0]["transaction_date"]}).json()
    assert c["balance"] == 150

    # can't confirm twice
    again = client.post(f"/api/proposals/{r['proposal_id']}/confirm", json={
        "index": 0, "customer_name": "Rajan", "transaction_type": "repayment", "amount": 200,
        "transaction_date": r["entries"][0]["transaction_date"]})
    assert again.status_code == 409


def test_demo_c_duplicate_needs_ack(monkeypatch):
    out = {"intent": "transactions", "entries": [
        E(customer_name="Rajan", transaction_type="credit_sale", amount=350)], "query_customer": None, "note": None}
    monkeypatch.setattr(extractor, "extract_text", stub(out))
    body = lambda d: {"index": 0, "customer_name": "Rajan", "transaction_type": "credit_sale",  # noqa: E731
                      "amount": 350, "transaction_date": d}
    r1 = client.post("/api/extract/text", json={"text": "Rajan 350 pattu"}).json()
    d = r1["entries"][0]["transaction_date"]
    client.post(f"/api/proposals/{r1['proposal_id']}/confirm", json=body(d))

    r2 = client.post("/api/extract/text", json={"text": "Rajan 350 pattu"}).json()
    assert "possible_duplicate" in r2["entries"][0]["flags"]
    blocked = client.post(f"/api/proposals/{r2['proposal_id']}/confirm", json=body(d))
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["flags"] == ["possible_duplicate"]
    assert client.get("/api/customers/Rajan").json()["balance"] == 350  # nothing silently added

    ok = client.post(f"/api/proposals/{r2['proposal_id']}/confirm", json={**body(d), "acknowledged": ["possible_duplicate"]})
    assert ok.status_code == 200 and ok.json()["balance"] == 700


def test_unclear_direction_cannot_be_confirmed_without_type(monkeypatch):
    monkeypatch.setattr(extractor, "extract_text", stub({"intent": "transactions", "entries": [
        E(customer_name="Latha", amount=300, flags=["unclear_direction"])], "query_customer": None, "note": None}))
    r = client.post("/api/extract/text", json={"text": "Latha 300"}).json()
    assert r["entries"][0]["status"] == "needs_review"
    bad = client.post(f"/api/proposals/{r['proposal_id']}/confirm", json={
        "index": 0, "customer_name": "Latha", "amount": 300, "transaction_date": "2026-10-27"})
    assert bad.status_code == 422  # transaction_type is required


def test_balance_query_answered_by_sql_not_model(monkeypatch):
    main.ledger.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=150, tx_date="2026-10-27")
    monkeypatch.setattr(extractor, "extract_text", stub({"intent": "balance_query", "entries": [],
                                                         "query_customer": "Rajan", "note": None}))
    r = client.post("/api/extract/text", json={"text": "what is Rajan's balance?"}).json()
    assert r["balance_answer"] == {"customer": "Rajan", "balance": 150.0}


def test_api_error_is_honest(monkeypatch):
    def boom(*a, **k):
        raise extractor.ExtractionError("Meta Model API call failed: 429")
    monkeypatch.setattr(extractor, "extract_text", boom)
    r = client.post("/api/extract/text", json={"text": "Rajan 350 pattu"})
    assert r.status_code == 502 and "429" in r.json()["detail"]


def test_missing_key_is_reported():
    with pytest.raises(extractor.ExtractionError, match="MODEL_API_KEY"):
        extractor._client()
