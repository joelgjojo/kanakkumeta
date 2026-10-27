"""input -> model -> deterministic checks -> stored proposal. Shared by the API and the eval runner."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from .extractor import Extraction
from .ledger import Ledger
from .schemas import ExtractionResult
from .validation import check_all


def build_proposal(ext: Extraction, *, ledger: Ledger, today: date, source_type: str,
                   source_ref: Optional[str], raw_input: Optional[str]) -> ExtractionResult:
    out = ext.output
    entries = check_all(out.entries, ledger=ledger, today=today, source_type=source_type) \
        if out.intent == "transactions" else []

    balance_answer = None
    if out.intent == "balance_query" and out.query_customer:
        c = ledger.find_customer(out.query_customer)
        # The answer comes from SQL, not from the model.
        balance_answer = {"customer": c["name"], "balance": ledger.balance(c["key"])} if c \
            else {"customer": out.query_customer, "balance": None, "message": "No such customer in the ledger"}

    pid = "p_" + uuid.uuid4().hex[:12]
    ledger.save_proposal(pid=pid, source_type=source_type, source_ref=source_ref, raw_input=raw_input,
                         model=ext.model, model_output=ext.raw,
                         proposed=[e.model_dump(mode="json") for e in entries])
    return ExtractionResult(
        proposal_id=pid, source_type=source_type, source_ref=source_ref, raw_input=raw_input,
        intent=out.intent, note=out.note, entries=entries, balance_answer=balance_answer,
        model=ext.model, cached=ext.cached, usage=ext.usage,
    )
