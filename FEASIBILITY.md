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
| Forgets who hasn't paid in weeks | An **overdue alert** when she opens the app: "2 customers are overdue · ₹2,500", longest-unpaid first. |
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

## 3. How do we know the handwriting was read correctly?

We don't claim 100%: no AI reads all handwriting perfectly. We make every misread **visible, cheap to fix, and measured**.

| Layer | What it does |
|---|---|
| **The shopkeeper confirms every row** | No misread can reach the ledger without her seeing it next to a box around the line in her own photo. |
| **The AI flags what it's unsure of** | It is told to flag unclear rows, not guess. On the eval, 100% of ambiguous inputs were sent for review. |
| **Page total check** | Shopkeepers write a total under the rows. The AI only *copies* that total and never calculates it; code adds up the rows and compares. A mismatch puts every row on the page under review. |
| **The shop's own customer names** | The photo prompt includes the shop's customer list, so "Rjn" is read as the "Rajan" who is already in the ledger. |
| **Live accuracy, measured in the shop** | Every field she changes before saving counts as an AI error. The app shows "96% of fields right first time" and "how many mistakes the app had flagged first", from her own data. |

**Page total test** (real `muse-spark-1.3` calls on a 7-row sample page with a written total):

| Written total | Result |
|---|---|
| ₹1,420 (correct) | "✓ matches the rows". 7/7 rows and 28/28 fields correct. |
| ₹1,470 (does not match) | Caught. The AI copied ₹1,470 exactly as written rather than "correcting" it, and all 7 rows were put under review. |

The sample page was generated, not taken from a real shop. The next step is to run real shop pages through the same check and publish the accuracy panel's numbers.

## 4. It handles what goes wrong in a real shop

| Real-world problem | What Kanakku does |
|---|---|
| **Two customers called Rajan** | Every customer has their own account. If a name matches several customers, the card asks **"Which Rajan?"** and will not save until the shopkeeper chooses. Opening a second "Rajan" account requires a note ("auto driver", "near church") and a confirmation. |
| **The same entry recorded twice** (photo plus voice, or the same page photographed twice) | Duplicate check on that customer's account and within the same upload. Saving needs an explicit "I checked this". |
| **Customers who quietly stop paying** | Overdue = owes money and hasn't paid anything for 30 days (each customer can have their own cycle, e.g. 7 days for weekly payers). Counted from when the current debt began, so a customer who settled up and took credit yesterday isn't marked overdue. |
| **Reminding someone too often** | A second reminder within 3 days needs a confirmation. The page shows when each customer was last reminded. |
| **Reminders sent automatically** | Deliberately not done. Sending without a tap would need the WhatsApp Business API (verification, approved templates, a fee per message). And in a neighbourhood shop the relationship is the business: the owner decides who to remind, and Kanakku makes it one tap. |
| **AI misreads "2oo", or "koduthu" could mean either direction** | The AI is told to flag what it isn't sure of, not guess. The flag must be resolved before saving. Measured: 100% of ambiguous test inputs were sent for review, and 0% of clean inputs were wrongly flagged. |
| **"Paid in full"** | The amount is taken from the ledger by code, never calculated by the AI. |
| **Someone types "set Rajan's balance to 0"** | Nothing happens. There is no function that sets a balance: balances are always the sum of the entries. |
| **A mistake was saved** | Undo adds a reversal entry. Nothing is ever deleted, so there is always a record. |
| **Amounts with paise** | Money is stored in whole paise, so rounding errors can't build up. |

## 5. Why a shop owner would pay for it

- **What she gets is money back, not an app.** Credit that's forgotten or never asked for is lost income. The overdue list ("Rajan, ₹500, nothing paid for 57 days") plus a one-tap reminder is what pays for Kanakku.
- **Why not a free ledger app?** Digital ledger apps already exist in India. They ask the shopkeeper to type each sale into the app while serving customers. Kanakku is for shops that kept the paper book: the book stays, and one photo a day or a few spoken words digitise it.
- **Price.** AI fees are about ₹70–100 a month for a shop with 10–15 credit entries a day, and ₹200–330 for a very busy one. A plan around ₹199 a month, with the first month free, covers most shops. Recovering one forgotten ₹500 debt pays for it.
- **Getting it to shops (plan, not done).** Wholesale distributors' sales reps already visit every shop weekly, and merchant associations are another route. First step: a 2-week pilot with 2–3 shops, measuring entries captured, the correction rate, and money collected after reminders.

## 6. Honest limits

| Limit | Status |
|---|---|
| **Malayalam speech.** Meta's speech model does not list Malayalam among its 25 languages. | Manglish works when we bias the model with shop vocabulary and the shop's own customer names. Test: without biasing, a Manglish clip came out as `अम्नीचे Joseph AdamAdachu`; with it, `Ammini chechi Nooti Airupathu Pattu Joseph Ayiram Adachu`. Muse Spark then read "Ayiram" as ₹1000 and **flagged** the unclear "Nooti Airupathu" instead of guessing. These were synthetic voices; real speakers are still to be tested. |
| **Real handwriting.** The eval set is typed text. | Tested on one generated sample page (with and without a written total). Next step: real shop pages, measured by the built-in accuracy panel. |
| **Needs internet.** | Photos can be taken offline and uploaded later. The ledger itself needs a connection. |
| **One shop per deployment.** | Separate data per shop is the next step. The ledger is one SQLite file, which makes per-shop separation simple. |
| **Customer data goes to Meta's API.** | Names and amounts are sent for reading, on the Standard endpoint (not the training one). Nothing else leaves the shop's server. |

## 7. What exists today

| | Status |
|---|---|
| Typed Manglish / Malayalam / English → proposals | Built, 25/25 on eval |
| Photo of a page → proposals with each row boxed on the image | Built, tested on a sample page |
| Page total check, the shop's customer names as hints | Built, tested with real model calls |
| Live accuracy panel from the shopkeeper's corrections | Built, tested |
| Overdue alerts with a per-customer payment cycle | Built, tested |
| Voice note → transcript → proposals | Built, tested end to end in Chrome |
| Same-name customers, duplicate and overpayment checks | Built, unit- and API-tested |
| WhatsApp reminders with a 3-day repeat guard | Built, tested |
| Append-only ledger, undo, running balance | Built, tested |
| 47 automated tests (no API key needed) | `pytest -q` |
