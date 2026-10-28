# Is Kanakku feasible?

Kanakku is a tool for small shops in Kerala that sell on credit and record it in a paper *pattu* book.
It turns that book into a ledger the shopkeeper can trust. This page covers whether it can work in a real shop,
at a real price, with today's models. Every number below was measured with this repository, unless it says otherwise.

## 1. It fits how the shop already works

| What the shopkeeper does today | What Kanakku asks of them |
|---|---|
| Writes `Rajan 350 pattu` in the book | Keep writing. At the end of the day, take **one photo** of the page. |
| Has no hands free while serving | Tap the mic and **say it**: "Rajan munnooru pattu". |
| Adds up a customer's debt by flipping pages | The balance is always shown, worked out from the entries. |
| Avoids asking customers for money because it's awkward | **One tap** opens a polite Malayalam + English reminder in the shop's own WhatsApp. |

- **No app to install, no training needed.** It is a web page that works in any phone browser.
- **No new habit.** The paper book stays the source the shopkeeper trusts, and Kanakku follows it.
- **The shopkeeper stays in charge.** The AI only *proposes* entries. Nothing is saved until the shopkeeper taps Save.

## 2. It is cheap enough to run

Measured on `muse-spark-1.3` at list price ($1.25 / $4.25 per 1M input/output tokens),
with `muse-voice-transcribe-1.0` at $0.18 per audio hour. Rupee figures assume $1 = ₹88.

| Input | Cost per entry | Median wait | How it was measured |
|---|---|---|---|
| Typed message, reasoning `minimal` | **$0.0026 (₹0.23)** | 1.6 s | 25-case eval, 25/25 passed |
| Typed message, API default | $0.0053 (₹0.47) | 3.4 s | same eval, same result |
| Voice note (~10 s) | ≈ $0.0031 (₹0.27) | + transcription | $0.0005 for the audio + the typed cost |
| Photo of a page | ≈ $0.0019 per row (₹0.17) | ~1 call per page | 7-row page, $0.0135 |

**A busy shop with 40 credit entries a day (1,200 a month) would cost about ₹200–330 a month in AI fees.**
Photos are cheapest because one call reads every row. Reminders cost nothing: they use a fixed template and the shop's own WhatsApp.

The same eval passed 25/25 at every reasoning level we tried (default, `low`, `minimal`). So we use `minimal` for typed and voice input,
which halved the cost and cut the median wait from 3.4 s to 1.6 s. Photos keep the API default until we have measured them on real handwriting.

We **chose not to** use the cheaper *Contributor* endpoint, which costs about 12× less but lets Meta train on the traffic. A record of who owes the shop money is private.

## 3. It handles what goes wrong in a real shop

| Real-world problem | What Kanakku does |
|---|---|
| **Two customers called Rajan** | Every customer has their own account. If a name matches several customers, the card asks **"Which Rajan?"** and will not save until the shopkeeper chooses. Opening a second "Rajan" account requires a note ("auto driver", "near church") and a confirmation. |
| **The same entry recorded twice** (photo plus voice, or the same page photographed twice) | Duplicate check on that customer's account and within the same upload. Saving needs an explicit "I checked this". |
| **Reminding someone too often** | A second reminder within 3 days needs a confirmation. The page shows when each customer was last reminded. |
| **AI misreads "2oo", or "koduthu" could mean either direction** | The AI is told to flag what it isn't sure of, not guess. The flag must be resolved before saving. Measured: 100% of ambiguous test inputs were sent for review, and 0% of clean inputs were wrongly flagged. |
| **"Paid in full"** | The amount is taken from the ledger by code, never calculated by the AI. |
| **Someone types "set Rajan's balance to 0"** | Nothing happens. There is no function that sets a balance: balances are always the sum of the entries. |
| **A mistake was saved** | Undo adds a reversal entry. Nothing is ever deleted, so there is always a record. |
| **Amounts with paise** | Money is stored in whole paise, so rounding errors can't build up. |

## 4. Honest limits

| Limit | Status |
|---|---|
| **Malayalam speech.** Meta's speech model does not list Malayalam among its 25 languages. | Manglish works when we bias the model with shop vocabulary and the shop's own customer names. Test: without biasing, a Manglish clip came out as `अम्नीचे Joseph AdamAdachu`; with it, `Ammini chechi Nooti Airupathu Pattu Joseph Ayiram Adachu`. Muse Spark then read "Ayiram" as ₹1000 and **flagged** the unclear "Nooti Airupathu" instead of guessing. These were synthetic voices; real speakers are still to be tested. |
| **Real handwriting.** The eval set is typed text. | One generated sample page has been tested. Next step: a set of real shop pages, with accuracy reported per row. |
| **Needs internet.** | Photos can be taken offline and uploaded later. The ledger itself needs a connection. |
| **One shop per deployment.** | Separate data per shop is the next step. The ledger is one SQLite file, which makes per-shop separation simple. |
| **Customer data goes to Meta's API.** | Names and amounts are sent for reading, on the Standard endpoint (not the training one). Nothing else leaves the shop's server. |

## 5. What exists today

| | Status |
|---|---|
| Typed Manglish / Malayalam / English → proposals | Built, 25/25 on eval |
| Photo of a page → proposals with each row boxed on the image | Built, tested on a sample page |
| Voice note → transcript → proposals | Built, tested end to end in Chrome |
| Same-name customers, duplicate and overpayment checks | Built, unit- and API-tested |
| WhatsApp reminders with a 3-day repeat guard | Built, tested |
| Append-only ledger, undo, running balance | Built, tested |
| 38 automated tests (no API key needed) | `pytest -q` |
