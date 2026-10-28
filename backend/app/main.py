from __future__ import annotations

import hashlib
import hmac
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import accuracy, demo, extractor, reminders
from .config import settings
from .ledger import AmbiguousCustomer, Ledger
from .pipeline import build_proposal
from .schemas import ConfirmRequest, CustomerUpdate, ExtractionResult, Flag, RemindRequest, SkipRequest
from .validation import plan_confirm

app = FastAPI(title="Kanakku API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])

ledger = Ledger(settings.data_dir / "kanakku.db")
UPLOADS = settings.data_dir / "uploads"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 32 * 1024 * 1024


def today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


# ---------- access code + daily AI cap: a public link must not be able to spend the shop's credits ----------
ACCESS_COOKIE = "kanakku_access"
OPEN_PATHS = {"/api/health", "/api/login"}


def _access_token() -> str:
    return hashlib.sha256(f"kanakku|{settings.access_code}".encode()).hexdigest()


@app.middleware("http")
async def require_access_code(request: Request, call_next):
    path = request.url.path
    if settings.access_code and path.startswith("/api/") and path not in OPEN_PATHS:
        if not hmac.compare_digest(request.cookies.get(ACCESS_COOKIE, ""), _access_token()):
            return JSONResponse({"detail": "access_code_required"}, status_code=401)
    return await call_next(request)


class LoginIn(BaseModel):
    code: str = Field(max_length=100)


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    if settings.access_code:
        if not hmac.compare_digest(body.code.strip(), settings.access_code):
            time.sleep(1)  # slow down guessing
            raise HTTPException(401, "Wrong access code")
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(ACCESS_COOKIE, _access_token(), max_age=60 * 60 * 24 * 30, httponly=True,
                            samesite="lax", secure=https)
    return {"ok": True}


_ai_calls = {"day": "", "count": 0}


def spend_ai_call() -> None:
    if not settings.max_ai_calls_per_day:
        return
    day = today().isoformat()
    if _ai_calls["day"] != day:
        _ai_calls.update(day=day, count=0)
    if _ai_calls["count"] >= settings.max_ai_calls_per_day:
        raise HTTPException(429, f"Today's AI limit ({settings.max_ai_calls_per_day} readings) is used up. "
                                 "Saved entries, balances and reminders still work.")
    _ai_calls["count"] += 1


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get("/api/health")
def health():
    return {"ok": True, "model": settings.model, "api_key_set": bool(settings.api_key), "today": today().isoformat(),
            "locked": bool(settings.access_code)}


@app.post("/api/extract/text", response_model=ExtractionResult)
def extract_text(body: TextIn):
    spend_ai_call()
    try:
        ext = extractor.extract_text(body.text, today().isoformat())
    except extractor.ExtractionError as e:
        raise HTTPException(502, detail=str(e))
    return build_proposal(ext, ledger=ledger, today=today(), source_type="text", source_ref=None, raw_input=body.text)


@app.post("/api/extract/image", response_model=ExtractionResult)
async def extract_image(file: UploadFile = File(...), hint: str = Form("")):
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image too large (max 10 MB)")
    spend_ai_call()
    mime = file.content_type or extractor.guess_mime(file.filename or "")
    ref = hashlib.sha256(data).hexdigest()[:16]
    ext_ = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / f"{ref}{ext_}").write_bytes(data)
    try:
        names = sorted({c["name"] for c in ledger.customers()})
        ext = extractor.extract_image(data, mime, today().isoformat(), hint, names)
    except extractor.ExtractionError as e:
        raise HTTPException(502, detail=str(e))
    return build_proposal(ext, ledger=ledger, today=today(), source_type="image",
                          source_ref=f"{ref}{ext_}", raw_input=hint or None)


@app.post("/api/extract/voice", response_model=ExtractionResult)
async def extract_voice(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Voice note too large (max 32 MB)")
    try:
        extractor.check_wav(data)
    except extractor.ExtractionError as e:
        raise HTTPException(422, detail=str(e))
    spend_ai_call()
    ref = hashlib.sha256(data).hexdigest()[:16] + ".wav"
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / ref).write_bytes(data)
    # The shop's own customer names are the words a general speech model is most likely to get wrong.
    names = sorted({c["name"] for c in ledger.customers()})
    try:
        asr = extractor.transcribe(data, names)
        if not asr["transcript"]:
            raise HTTPException(422, "Couldn't hear anything. Try again, a little closer to the phone.")
        ext = extractor.extract_voice_transcript(asr["transcript"], today().isoformat())
    except extractor.ExtractionError as e:
        raise HTTPException(502, detail=str(e))
    res = build_proposal(ext, ledger=ledger, today=today(), source_type="voice", source_ref=ref,
                         raw_input=asr["transcript"])
    res.usage = {**(res.usage or {}), "asr": {k: asr[k] for k in ("duration_ms", "cost_usd", "cached")}}
    return res


@app.get("/api/sources/{ref}")
def get_source(ref: str):
    path = (UPLOADS / ref).resolve()
    if path.parent != UPLOADS.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/proposals/{pid}")
def get_proposal(pid: str):
    p = ledger.get_proposal(pid)
    if not p:
        raise HTTPException(404, "proposal not found")
    return p


@app.post("/api/proposals/{pid}/confirm")
def confirm(pid: str, body: ConfirmRequest):
    p = ledger.get_proposal(pid)
    if not p:
        raise HTTPException(404, "proposal not found")
    status = p["entry_status"].get(str(body.index))
    if status is None:
        raise HTTPException(404, "no such entry in this proposal")
    if status != "pending":
        raise HTTPException(409, f"entry already {status}")
    original = p["proposed"][body.index]
    try:
        phone = reminders.normalize_phone(body.customer_phone)
        plan = plan_confirm(
            ledger=ledger, name=body.customer_name, customer_id=body.customer_id, new_customer=body.new_customer,
            label=body.customer_label, tx_type=body.transaction_type, amount=body.amount,
            tx_date=body.transaction_date, today=today(), original_flags=original["flags"])
    except AmbiguousCustomer as e:
        raise HTTPException(409, detail={"error": "customer_choice_required", "candidates": e.candidates})
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    missing = plan.need - set(body.acknowledged)
    if missing:
        # Nothing is saved until the human has explicitly seen every warning.
        raise HTTPException(409, detail={"error": "acknowledgement_required",
                                         "flags": sorted(f.value for f in missing)})
    cid = plan.customer_id or ledger.create_customer(body.customer_name, body.customer_label, phone)
    tx = ledger.add_transaction(
        customer_id=cid, tx_type=body.transaction_type.value, amount=body.amount,
        tx_date=body.transaction_date.isoformat(), description=body.description, proposal_id=pid,
        entry_index=body.index, source_ref=p["source_ref"], acknowledged=[f.value for f in body.acknowledged])
    ledger.set_entry_status(pid, body.index, "confirmed")
    c = ledger.get_customer(cid)
    return {"transaction": tx, "customer": c["name"], "customer_id": cid, "label": c["label"],
            "balance": c["balance"]}


@app.post("/api/proposals/{pid}/skip")
def skip(pid: str, body: SkipRequest):
    p = ledger.get_proposal(pid)
    if not p or str(body.index) not in p["entry_status"]:
        raise HTTPException(404)
    if p["entry_status"][str(body.index)] != "pending":
        raise HTTPException(409, "entry already handled")
    ledger.set_entry_status(pid, body.index, "skipped")
    return {"ok": True}


@app.get("/api/customers")
def customers():
    return ledger.customers()


@app.get("/api/customers/{cid}")
def customer(cid: str):
    c = ledger.get_customer(cid)
    if not c:
        raise HTTPException(404, "customer not found")
    return {**c, "history": ledger.history(cid), "last_reminder": ledger.last_reminder(cid)}


@app.patch("/api/customers/{cid}")
def update_customer(cid: str, body: CustomerUpdate):
    if not ledger.get_customer(cid):
        raise HTTPException(404, "customer not found")
    phone = None  # None = leave unchanged, "" = remove
    if body.phone is not None:
        try:
            phone = reminders.normalize_phone(body.phone) or ""
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
    return ledger.update_customer(cid, label=body.label, phone=phone, overdue_days=body.overdue_days)


@app.get("/api/reminders")
def reminder_list():
    return reminders.due(ledger, today().isoformat())


@app.post("/api/customers/{cid}/remind")
def remind(cid: str, body: RemindRequest):
    c = ledger.get_customer(cid)
    if not c:
        raise HTTPException(404, "customer not found")
    if c["balance"] <= 0:
        raise HTTPException(409, detail={"error": "nothing_owed"})
    last = ledger.last_reminder(cid)
    if last and "recent_reminder" not in body.acknowledged and \
            datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"]) < reminders.MIN_GAP:
        # Same customer, reminded a moment ago: ask before nagging them again.
        raise HTTPException(409, detail={"error": "acknowledgement_required", "flags": ["recent_reminder"],
                                         "last_reminded_at": last["created_at"]})
    text = reminders.message(c["name"], c["balance"])
    logged = ledger.log_reminder(cid, c["balance"], text)
    return {"message": text, "whatsapp_url": reminders.whatsapp_url(c["phone"], text),
            "reminded_at": logged["created_at"], "has_phone": bool(c["phone"])}


@app.post("/api/transactions/{tx_id}/reverse")
def reverse(tx_id: str, reason: str = ""):
    try:
        return ledger.reverse_transaction(tx_id, reason)
    except KeyError:
        raise HTTPException(404)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/summary")
def summary(day: Optional[str] = None):
    out = ledger.day_summary(day or today().isoformat())
    overdue = [r for r in reminders.due(ledger, today().isoformat()) if r["overdue"]]
    out["overdue"] = {"count": len(overdue), "total": sum(r["balance"] for r in overdue),
                      "default_days": settings.overdue_days}
    return out


@app.post("/api/demo")
def load_demo():
    """Fill an EMPTY ledger with a small fictional shop, so a first-time visitor sees overdue, same names, etc."""
    if ledger.customers():
        raise HTTPException(409, "The ledger already has customers. The demo only loads into an empty ledger.")
    demo.seed(ledger, today())
    return {"ok": True, "customers": len(ledger.customers())}


@app.get("/api/accuracy")
def accuracy_report(days: int = 30):
    return accuracy.report(ledger, days)


@app.get("/api/flags")
def flag_list():
    from .validation import MESSAGES
    return {f.value: MESSAGES[f] for f in Flag}


# The review UI. Mounted last so it never shadows an /api route.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
