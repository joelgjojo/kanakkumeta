"""Typed shapes for everything that crosses a boundary.

ModelEntry / ModelOutput  = what Muse Spark is allowed to say (enforced by structured output).
ProposedEntry             = what the app shows the shopkeeper after deterministic checks.
ConfirmRequest            = what the shopkeeper sends back.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TxType(str, Enum):
    credit_sale = "credit_sale"   # customer took goods on credit (pattu / kadam) -> balance goes UP
    repayment = "repayment"       # customer paid back (thannu / adachu)          -> balance goes DOWN


class Flag(str, Enum):
    # Flags the MODEL may raise (it is told to raise these instead of guessing)
    unclear_direction = "unclear_direction"
    missing_customer = "missing_customer"
    missing_amount = "missing_amount"
    uncertain_amount = "uncertain_amount"
    illegible = "illegible"
    missing_date = "missing_date"
    # Flags only CODE raises
    invalid_amount = "invalid_amount"
    amount_from_balance = "amount_from_balance"
    possible_duplicate = "possible_duplicate"
    similar_customer = "similar_customer"
    new_customer = "new_customer"
    overpayment = "overpayment"
    invalid_date = "invalid_date"
    ambiguous_customer = "ambiguous_customer"    # 2+ customers share this name: pick one
    same_name_customer = "same_name_customer"    # creating a second account with an existing name
    page_total_mismatch = "page_total_mismatch"  # rows don't add up to the total written on the page


MODEL_FLAGS = [
    Flag.unclear_direction, Flag.missing_customer, Flag.missing_amount,
    Flag.uncertain_amount, Flag.illegible, Flag.missing_date,
]

# Hard flags = a required field is missing/invalid. Cannot be confirmed until the user fills it.
HARD_FLAGS = {
    Flag.unclear_direction, Flag.missing_customer, Flag.missing_amount,
    Flag.invalid_amount, Flag.illegible, Flag.missing_date, Flag.invalid_date, Flag.ambiguous_customer,
}
# Soft flags = values exist but a human must explicitly acknowledge them.
SOFT_FLAGS = {
    Flag.uncertain_amount, Flag.amount_from_balance, Flag.possible_duplicate,
    Flag.similar_customer, Flag.overpayment, Flag.same_name_customer, Flag.page_total_mismatch,
}
# Info flags = shown, never block.
INFO_FLAGS = {Flag.new_customer}


class ModelEntry(BaseModel):
    customer_name: Optional[str] = None
    transaction_type: Optional[TxType] = None
    amount: Optional[float] = None
    amount_text: Optional[str] = None          # the amount exactly as written, e.g. "2oo", "-350"
    transaction_date: Optional[str] = None     # YYYY-MM-DD
    description: Optional[str] = None
    paid_in_full: bool = False
    flags: list[Flag] = Field(default_factory=list)
    evidence: Optional[str] = None             # the exact snippet of input this came from
    box: Optional[list[int]] = None            # image only: [x_min, y_min, x_max, y_max] on 0-1000 grid


class ModelOutput(BaseModel):
    intent: Literal["transactions", "balance_query", "not_ledger", "unclear"]
    entries: list[ModelEntry] = Field(default_factory=list)
    query_customer: Optional[str] = None
    note: Optional[str] = None
    written_total: Optional[float] = None      # photos only: the total the shopkeeper wrote, never computed
    written_total_text: Optional[str] = None


class ProposedEntry(BaseModel):
    index: int
    customer_name: Optional[str]
    transaction_type: Optional[TxType]
    amount: Optional[float]
    transaction_date: Optional[str]
    description: Optional[str]
    evidence: Optional[str]
    box: Optional[list[int]]
    flags: list[Flag]
    flag_messages: dict[str, str]
    matched_customer: Optional[str] = None     # existing ledger name this maps to, if any
    matched_customer_id: Optional[str] = None
    candidates: list[dict] = Field(default_factory=list)  # every existing customer with this name
    similar_to: Optional[str] = None
    duplicate_of: Optional[str] = None         # transaction id
    current_balance: Optional[float] = None
    status: Literal["needs_review", "ready"]


class ExtractionResult(BaseModel):
    proposal_id: str
    source_type: Literal["text", "image", "voice"]
    source_ref: Optional[str]
    raw_input: Optional[str]
    intent: str
    note: Optional[str]
    entries: list[ProposedEntry]
    balance_answer: Optional[dict] = None
    page_total: Optional[dict] = None          # photos: written total vs the rows, checked in code
    model: str
    cached: bool
    usage: Optional[dict] = None


class ConfirmRequest(BaseModel):
    index: int
    customer_name: str
    # Which existing customer this is. Required when several share the name.
    customer_id: Optional[str] = None
    # True = open a NEW account even though the name exists. Needs a label to tell them apart.
    new_customer: bool = False
    customer_label: Optional[str] = Field(default=None, max_length=60)
    customer_phone: Optional[str] = Field(default=None, max_length=20)
    transaction_type: TxType
    amount: float = Field(gt=0, le=10_000_000)
    transaction_date: date
    description: Optional[str] = None
    acknowledged: list[Flag] = Field(default_factory=list)


class SkipRequest(BaseModel):
    index: int


class CustomerUpdate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=60)
    phone: Optional[str] = Field(default=None, max_length=20)
    overdue_days: Optional[int] = Field(default=None, ge=0, le=365)  # 0 = use the shop default


class RemindRequest(BaseModel):
    acknowledged: list[str] = Field(default_factory=list)
