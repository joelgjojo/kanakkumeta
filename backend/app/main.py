from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import extractor
from .config import settings
from .ledger import Ledger, customer_key
from .pipeline import build_proposal
from .schemas import ConfirmRequest, ExtractionResult, Flag, SkipRequest
from .validation import soft_flags_for_confirm

app = FastAPI(title="Kanakku API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])

ledger = Ledger(settings.data_dir / "kanakku.db")
UPLOADS = settings.data_dir / "uploads"
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get("/api/health")
def health():
    return {"ok": True, "model": settings.model, "api_key_set": bool(settings.api_key), "today": today().isoformat()}


@app.post("/api/extract/text", response_model=ExtractionResult)
def extract_text(body: TextIn):
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
    mime = file.content_type or extractor.guess_mime(file.filename or "")
    ref = hashlib.sha256(data).hexdigest()[:16]
    ext_ = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / f"{ref}{ext_}").write_bytes(data)
    try:
        ext = extractor.extract_image(data, mime, today().isoformat(), hint)
    except extractor.ExtractionError as e:
        raise HTTPException(502, detail=str(e))
    return build_proposal(ext, ledger=ledger, today=today(), source_type="image",
                          source_ref=f"{ref}{ext_}", raw_input=hint or None)


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
        need = soft_flags_for_confirm(
            ledger=ledger, name=body.customer_name, tx_type=body.transaction_type, amount=body.amount,
            tx_date=body.transaction_date, today=today(), original_flags=original["flags"])
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    missing = need - set(body.acknowledged)
    if missing:
        # Nothing is saved until the human has explicitly seen every warning.
        raise HTTPException(409, detail={"error": "acknowledgement_required",
                                         "flags": sorted(f.value for f in missing)})
    tx = ledger.add_transaction(
        customer_name=body.customer_name, tx_type=body.transaction_type.value, amount=body.amount,
        tx_date=body.transaction_date.isoformat(), description=body.description, proposal_id=pid,
        entry_index=body.index, source_ref=p["source_ref"], acknowledged=[f.value for f in body.acknowledged])
    ledger.set_entry_status(pid, body.index, "confirmed")
    key = customer_key(body.customer_name)
    return {"transaction": tx, "customer": ledger.find_customer(body.customer_name)["name"],
            "balance": ledger.balance(key)}


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


@app.get("/api/customers/{name}")
def customer(name: str):
    c = ledger.find_customer(name)
    if not c:
        raise HTTPException(404, "customer not found")
    return {"name": c["name"], "balance": ledger.balance(c["key"]), "history": ledger.history(c["key"])}


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
    return ledger.day_summary(day or today().isoformat())


@app.get("/api/flags")
def flag_list():
    from .validation import MESSAGES
    return {f.value: MESSAGES[f] for f in Flag}


# The review UI. Mounted last so it never shadows an /api route.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
