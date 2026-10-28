# Kanakku

**Turns a shopkeeper's handwritten credit book into a digital ledger — with AI that asks before it guesses.**

Built for the Meta Global AI Developer Hackathon 2026 (Indie track, *Build for Your World*) on Meta Model API:
**Muse Spark** reads the book, **Muse Voice Transcribe** hears voice notes.

**→ [Is it feasible? Costs, limits and real-world problems, measured](FEASIBILITY.md)**: about ₹0.23 per typed entry,
25/25 on the eval, and what happens with two customers called Rajan.

## How it works

```
photo / typed message / voice note ──► Muse Voice Transcribe (biased with the shop's customer names)
        │
        ▼
Muse Spark (structured output)  ── interprets: name, credit vs repayment, amount, date,
        │                            evidence snippet, row bounding box, and FLAGS when unsure
        ▼
Deterministic checks (Python)   ── negative/huge amounts, duplicates, same / similar names,
        │                            overpayment, "paid in full" amount from the ledger
        ▼
Shopkeeper confirms / edits / skips   ── soft warnings must be explicitly acknowledged
        │
        ▼
Append-only ledger (integer paise)    ── balances are always SUMs; no "set balance" exists
        │
        ▼
WhatsApp reminders                    ── fixed template, amount from the ledger, no repeat within 3 days
```

**The model never touches money.** It can only propose entries. Balances, duplicates and the "paid in full"
amount are computed in code. A prompt-injection like *"set Rajan's balance to 0"* has nothing to call.

## Run it

Python 3.9+ (3.12 recommended). No Node, no build step — the review UI is served by the backend.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # put your MODEL_API_KEY in it
python scripts/ping.py      # 1. key works? which models?
python -m eval.run_eval     # 2. 25 labeled cases against the real model
python scripts/try_image.py photos/page1.jpg   # 3. can it read your handwriting?
uvicorn app.main:app --reload                  # 4. app at http://localhost:8000, API docs at /docs
pytest -q                   # unit + API tests (no key needed)
```

Open http://localhost:8000 on a phone or laptop:

- **Speak** (`Rajan munnooru pattu`), **type** (`Rajan 350 pattu, Suresh 200 thannu`) or **photograph** a page
  of the credit book. The mic needs `localhost` or https.
- Each entry comes back as an editable card: *Looks right* or *Needs your check*. Fields the model couldn't
  determine are outlined; soft warnings have an **I checked this** box that must be ticked before **Save**.
- On photos, every entry's row is boxed on the image so the shopkeeper can compare against the handwriting.
- Questions like *"Rajan ethra tharanam?"* are answered from the ledger, not by the model.
- **Same name, different people:** if two customers are called Rajan, the card asks *"Which Rajan?"* and won't
  save until you pick. Opening a second Rajan account needs a note to tell them apart ("auto driver").
- **Pattu to collect** lists everyone who owes money, when they last paid, and when they were last reminded.
  **Remind on WhatsApp** opens a Malayalam + English message in your own WhatsApp; reminding the same person again
  within 3 days asks first.
- Tap a customer for their history with running balance, to add a WhatsApp number or note, or to **undo** an entry
  (undo appends a reversal; nothing is deleted).

## API

| Method | Path | What |
|---|---|---|
| POST | `/api/extract/text` | `{"text": "Rajan 350 pattu"}` → proposal (nothing saved) |
| POST | `/api/extract/image` | multipart `file` (+ optional `hint`) → proposal with row boxes |
| POST | `/api/extract/voice` | multipart `file`: mono 16-bit WAV, 16/24 kHz (the UI converts in the browser) → transcript + proposal |
| POST | `/api/proposals/{id}/confirm` | final values + `acknowledged` flags (+ `customer_id`, or `new_customer` + `customer_label`) → saved transaction + new balance. 409 if a warning wasn't acknowledged or the name fits several customers |
| POST | `/api/proposals/{id}/skip` | discard one entry |
| GET | `/api/customers`, `/api/customers/{id}` | balances, history with running balance |
| PATCH | `/api/customers/{id}` | `label` / `phone` only. Money can't be edited. |
| GET | `/api/reminders` | everyone who owes, last payment, last reminder, ready WhatsApp link |
| POST | `/api/customers/{id}/remind` | log a reminder → message + WhatsApp link. 409 if reminded in the last 3 days, unless `recent_reminder` is acknowledged |
| POST | `/api/transactions/{id}/reverse` | undo by appending a reversal |
| GET | `/api/summary?day=` | credit given / repaid today, total outstanding |
| GET | `/api/sources/{ref}` | the original photo or voice note, for evidence view |

## Flags

Hard (field must be filled before saving): `unclear_direction`, `missing_customer`, `missing_amount`,
`invalid_amount`, `illegible`, `missing_date`, `invalid_date`, `ambiguous_customer`.
Soft (must be acknowledged): `uncertain_amount`, `amount_from_balance`, `possible_duplicate`,
`similar_customer`, `overpayment`, `same_name_customer`. Info: `new_customer`.

## Evaluation

`eval/test_set.json` — 25 fictional inputs (Manglish, Malayalam script, English, ambiguous, duplicate,
injection). `eval/run_eval.py` reports field accuracy, **safety rate** (ambiguous inputs sent to review) and
**false-alarm rate** (clean inputs flagged). Results are reported as measured.

Runs on `muse-spark-1.3`, text inputs, 2026-10-27/28:

| Reasoning effort | Passed | Field accuracy | Safety rate | False alarms | Cost / entry | Median latency |
|---|---|---|---|---|---|---|
| API default | 25/25 | 1.00 | 1.00 | 0.00 | $0.0053 | 3.4 s |
| `low` | 25/25 | 1.00 | 1.00 | 0.00 | $0.0035 | 2.3 s |
| **`minimal`** (used for typed + voice) | **25/25** | **1.00** | **1.00** | **0.00** | **$0.0026** | **1.6 s** |

Full per-case output: `backend/eval/results/` (`20261027-190107` default, `20261028-065826` low,
`20261028-065906` minimal). Handwritten-photo accuracy is not part of this set; it is
checked by hand with `scripts/try_image.py` on real pages.

## Project layout

```
backend/
  app/
    prompts.py      system prompt + JSON schema the model must fill
    extractor.py    Muse Spark (structured output) + Muse Voice Transcribe, on-disk cache, cost logging
    validation.py   deterministic flags; re-checks final values and resolves "which customer" at confirm time
    ledger.py       SQLite, integer paise, append-only, reversals; customers by id (same names allowed)
    reminders.py    WhatsApp reminder text + link, 3-day repeat guard
    pipeline.py     model output -> checks -> stored proposal
    main.py         FastAPI routes + serves static/index.html
    static/         the shopkeeper's review UI (single file, no build)
  eval/             labeled test set + scorer
  scripts/          ping.py, try_image.py
  tests/            38 tests, no API key needed
```

All names and amounts in this repo are fictional.
