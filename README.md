# Kanakku

**Turns a shopkeeper's handwritten credit book into a digital ledger — with AI that asks before it guesses.**

Built for the Meta Global AI Developer Hackathon 2026 (Indie track, *Build for Your World*) on Muse Spark via Meta Model API.

## How it works

```
photo / Manglish message
        │
        ▼
Muse Spark (structured output)  ── interprets: name, credit vs repayment, amount, date,
        │                            evidence snippet, row bounding box, and FLAGS when unsure
        ▼
Deterministic checks (Python)   ── negative/huge amounts, duplicates, similar names,
        │                            overpayment, "paid in full" amount from the ledger
        ▼
Shopkeeper confirms / edits / skips   ── soft warnings must be explicitly acknowledged
        │
        ▼
Append-only ledger (integer paise)    ── balances are always SUMs; no "set balance" exists
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

- **Type** a message (`Rajan 350 pattu, Suresh 200 thannu`) or **photograph** a page of the credit book.
- Each entry comes back as an editable card: *Looks right* or *Needs your check*. Fields the model couldn't
  determine are outlined; soft warnings have an **I checked this** box that must be ticked before **Save**.
- On photos, every entry's row is boxed on the image so the shopkeeper can compare against the handwriting.
- Questions like *"Rajan ethra tharanam?"* are answered from the ledger, not by the model.
- Tap a customer for their history with running balance; **undo** appends a reversal (nothing is deleted).

## API

| Method | Path | What |
|---|---|---|
| POST | `/api/extract/text` | `{"text": "Rajan 350 pattu"}` → proposal (nothing saved) |
| POST | `/api/extract/image` | multipart `file` (+ optional `hint`) → proposal with row boxes |
| POST | `/api/proposals/{id}/confirm` | final values + `acknowledged` flags → saved transaction + new balance. 409 if a warning wasn't acknowledged |
| POST | `/api/proposals/{id}/skip` | discard one entry |
| GET | `/api/customers`, `/api/customers/{name}` | balances, history with running balance |
| POST | `/api/transactions/{id}/reverse` | undo by appending a reversal |
| GET | `/api/summary?day=` | credit given / repaid today, total outstanding |
| GET | `/api/sources/{ref}` | the original photo, for evidence view |

## Flags

Hard (field must be filled before saving): `unclear_direction`, `missing_customer`, `missing_amount`,
`invalid_amount`, `illegible`, `missing_date`, `invalid_date`.
Soft (must be acknowledged): `uncertain_amount`, `amount_from_balance`, `possible_duplicate`,
`similar_customer`, `overpayment`. Info: `new_customer`.

## Evaluation

`eval/test_set.json` — 25 fictional inputs (Manglish, Malayalam script, English, ambiguous, duplicate,
injection). `eval/run_eval.py` reports field accuracy, **safety rate** (ambiguous inputs sent to review) and
**false-alarm rate** (clean inputs flagged). Results are reported as measured.

Latest run (`muse-spark-1.3`, 2026-10-27, text inputs, default reasoning effort):

| Metric | Result |
|---|---|
| Cases passed | 25 / 25 |
| Field accuracy (name, type, amount, date) | 1.00 |
| Safety rate (ambiguous / injection inputs sent to review) | 1.00 |
| False-alarm rate (clean inputs flagged) | 0.00 |
| Cost of the full run | ≈ $0.13 |

Full per-case output: `backend/eval/results/`. Handwritten-photo accuracy is not part of this set; it is
checked by hand with `scripts/try_image.py` on real pages.

## Project layout

```
backend/
  app/
    prompts.py      system prompt + JSON schema the model must fill
    extractor.py    Meta Model API call (structured output), on-disk response cache, cost logging
    validation.py   deterministic flags; re-checks final values at confirm time
    ledger.py       SQLite, integer paise, append-only, reversals instead of deletes
    pipeline.py     model output -> checks -> stored proposal
    main.py         FastAPI routes + serves static/index.html
    static/         the shopkeeper's review UI (single file, no build)
  eval/             labeled test set + scorer
  scripts/          ping.py, try_image.py
  tests/            26 tests, no API key needed
```

All names and amounts in this repo are fictional.
