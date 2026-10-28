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
    assert client.get("/api/customers").json()[0]["balance"] == 350  # nothing silently added

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


def _propose(monkeypatch, name="Rajan", amount=350, tx="credit_sale"):
    monkeypatch.setattr(extractor, "extract_text", stub({"intent": "transactions", "entries": [
        E(customer_name=name, transaction_type=tx, amount=amount)], "query_customer": None, "note": None}))
    return client.post("/api/extract/text", json={"text": f"{name} {amount}"}).json()


def test_same_name_flow(monkeypatch):
    body = {"index": 0, "customer_name": "Rajan", "transaction_type": "credit_sale", "amount": 350,
            "transaction_date": "2026-10-27"}
    r = _propose(monkeypatch)
    first = client.post(f"/api/proposals/{r['proposal_id']}/confirm", json=body).json()

    # A different Rajan: opening a second account needs a note AND an explicit ack.
    r = _propose(monkeypatch, amount=90)
    url = f"/api/proposals/{r['proposal_id']}/confirm"
    b2 = {**body, "amount": 90, "new_customer": True}
    assert client.post(url, json=b2).status_code == 422
    blocked = client.post(url, json={**b2, "customer_label": "auto driver"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["flags"] == ["same_name_customer"]
    second = client.post(url, json={**b2, "customer_label": "auto driver", "customer_phone": "9847012345",
                                    "acknowledged": ["same_name_customer"]}).json()
    assert second["customer_id"] != first["customer_id"] and second["balance"] == 90

    # Now "Rajan" alone is ambiguous: extraction asks, and confirm refuses to guess.
    r = _propose(monkeypatch, amount=20)
    e = r["entries"][0]
    assert "ambiguous_customer" in e["flags"] and len(e["candidates"]) == 2
    url = f"/api/proposals/{r['proposal_id']}/confirm"
    guess = client.post(url, json={**body, "amount": 20})
    assert guess.status_code == 409 and guess.json()["detail"]["error"] == "customer_choice_required"
    ok = client.post(url, json={**body, "amount": 20, "customer_id": second["customer_id"]}).json()
    assert ok["balance"] == 110 and ok["label"] == "auto driver"
    assert client.get(f"/api/customers/{first['customer_id']}").json()["balance"] == 350


def test_reminder_flow_and_no_double_reminder(monkeypatch):
    r = _propose(monkeypatch)
    c = client.post(f"/api/proposals/{r['proposal_id']}/confirm", json={
        "index": 0, "customer_name": "Rajan", "transaction_type": "credit_sale", "amount": 350,
        "transaction_date": "2026-10-27"}).json()
    cid = c["customer_id"]
    assert client.patch(f"/api/customers/{cid}", json={"phone": "123"}).status_code == 422
    assert client.patch(f"/api/customers/{cid}", json={"phone": "98470 12345"}).json()["phone"] == "919847012345"

    due = client.get("/api/reminders").json()
    assert due[0]["balance"] == 350 and not due[0]["recently_reminded"]
    sent = client.post(f"/api/customers/{cid}/remind", json={}).json()
    assert sent["whatsapp_url"].startswith("https://wa.me/919847012345?text=") and "₹350" in sent["message"]

    again = client.post(f"/api/customers/{cid}/remind", json={})
    assert again.status_code == 409 and again.json()["detail"]["flags"] == ["recent_reminder"]
    assert client.get("/api/reminders").json()[0]["recently_reminded"]
    assert client.post(f"/api/customers/{cid}/remind", json={"acknowledged": ["recent_reminder"]}).status_code == 200


def test_voice_endpoint(monkeypatch):
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)  # noqa: E702
    seen = {}

    def fake_asr(wav, keywords):
        seen["keywords"] = keywords
        return {"transcript": "Rajan munnoottambathu pattu", "duration_ms": 1000, "cost_usd": 0.00005, "cached": False}
    monkeypatch.setattr(extractor, "transcribe", fake_asr)
    monkeypatch.setattr(extractor, "extract_voice_transcript", stub({"intent": "transactions", "entries": [
        E(customer_name="Rajan", transaction_type="credit_sale", amount=350, amount_text="munnoottambathu")],
        "query_customer": None, "note": None}))
    main.ledger.add_transaction(customer_name="Rajan", tx_type="credit_sale", amount=10, tx_date="2026-10-20")
    r = client.post("/api/extract/voice", files={"file": ("v.wav", buf.getvalue(), "audio/wav")}).json()
    assert r["source_type"] == "voice" and r["raw_input"] == "Rajan munnoottambathu pattu"
    assert r["entries"][0]["amount"] == 350 and r["usage"]["asr"]["duration_ms"] == 1000
    assert seen["keywords"] == ["Rajan"]  # the shop's customer names bias the speech model

    bad = client.post("/api/extract/voice", files={"file": ("v.mp3", b"ID3 not a wav", "audio/mpeg")})
    assert bad.status_code == 422
