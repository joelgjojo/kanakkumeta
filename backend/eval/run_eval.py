"""Run the labeled test set against the REAL model and score it.

    python -m eval.run_eval                 # all cases (cached responses are reused, free)
    python -m eval.run_eval --only T14,T15  # some cases
    python -m eval.run_eval --no-cache      # force fresh calls (costs credits)
    python -m eval.run_eval --dry-run       # show what would be sent, no API calls

Writes eval/results/<timestamp>.json and .md. These numbers go in the submission as-is, good or bad.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import extractor  # noqa: E402
from app.config import settings  # noqa: E402
from app.ledger import Ledger, customer_key  # noqa: E402
from app.pipeline import build_proposal  # noqa: E402
from app.prompts import USER_TEXT_TEMPLATE  # noqa: E402

INTENT_FLAGS = {"not_a_ledger_entry": "not_ledger", "query_not_transaction": "balance_query"}
FIELDS = ["customer_name", "transaction_type", "amount", "transaction_date"]


def norm(field, v):
    if v is None:
        return None
    if field == "customer_name":
        return customer_key(str(v))
    if field == "transaction_type":
        return v.value if hasattr(v, "value") else v
    if field == "amount":
        return round(float(v), 2)
    return str(v)


def score_case(case: dict, result) -> dict:
    exp, exp_flags = case["expected"], set(case["expected_flags"])
    entries = result.entries
    got_flags = {f.value for e in entries for f in e.flags}
    problems: list[str] = []
    field_hits = field_total = 0

    intent_flag = next((f for f in exp_flags if f in INTENT_FLAGS), None)
    if intent_flag:
        want = INTENT_FLAGS[intent_flag]
        if result.intent != want:
            problems.append(f"intent={result.intent}, wanted {want}")
        if entries:
            problems.append(f"produced {len(entries)} entries, wanted none")
        safe = result.intent == want and not entries
        return {"pass": not problems, "problems": problems, "field_hits": 0, "field_total": 0,
                "kind": "intent", "safe": safe, "false_alarm": False}

    if result.intent != "transactions":
        problems.append(f"intent={result.intent}, wanted transactions")
    if len(entries) != len(exp):
        problems.append(f"{len(entries)} entries, wanted {len(exp)}")
    for i, want in enumerate(exp):
        got = entries[i] if i < len(entries) else None
        for f in FIELDS:
            field_total += 1
            g = norm(f, getattr(got, f)) if got else "<missing>"
            w = norm(f, want.get(f))
            if g == w:
                field_hits += 1
            else:
                problems.append(f"#{i + 1} {f}: got {g!r}, wanted {w!r}")

    missing_flags = exp_flags - got_flags
    if missing_flags:
        problems.append(f"missing flags {sorted(missing_flags)}")
    any_review = any(e.status == "needs_review" for e in entries)
    if exp_flags:
        kind, safe, false_alarm = "ambiguous", any_review and not missing_flags, False
    else:
        kind, safe = "clean", True
        false_alarm = any_review
        if false_alarm:
            blocking = sorted({f.value for e in entries for f in e.flags if f.value != "new_customer"})
            problems.append(f"false alarm: flagged {blocking}")
    return {"pass": not problems, "problems": problems, "field_hits": field_hits, "field_total": field_total,
            "kind": kind, "safe": safe, "false_alarm": false_alarm}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(HERE / "test_set.json"))
    ap.add_argument("--only", default="")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.no_cache:
        os.environ["KANAKKU_CACHE"] = "0"
        object.__setattr__(settings, "cache", False)

    data = json.loads(Path(args.set).read_text())
    today = date.fromisoformat(data["reference_date"])
    cases = data["cases"]
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        cases = [c for c in cases if c["id"] in keep]

    if args.dry_run:
        print(f"Model: {settings.model}  base: {settings.base_url}  cache: {settings.cache}")
        print(USER_TEXT_TEMPLATE.format(today=today, text=cases[0]["input"]))
        print(f"\n{len(cases)} cases would be sent. No calls made.")
        return

    rows, cost, calls, cached = [], 0.0, 0, 0
    print(f"Running {len(cases)} cases on {settings.model} (cache={'on' if settings.cache else 'off'})\n")
    for c in cases:
        L = Ledger(":memory:")
        for s in c.get("seed", []):
            L.add_transaction(**s)
        t0 = time.monotonic()
        try:
            ext = extractor.extract_text(c["input"], today.isoformat())
        except extractor.ExtractionError as e:
            rows.append({"id": c["id"], "input": c["input"], "error": str(e), "pass": False, "safe": False,
                         "kind": "error", "problems": [str(e)], "field_hits": 0, "field_total": 0,
                         "false_alarm": False})
            print(f"  {c['id']} ERROR  {e}")
            continue
        res = build_proposal(ext, ledger=L, today=today, source_type="text", source_ref=None, raw_input=c["input"])
        sc = score_case(c, res)
        calls += 1
        cached += ext.cached
        if ext.usage and ext.usage.get("cost_usd"):
            cost += ext.usage["cost_usd"]
        rows.append({"id": c["id"], "input": c["input"], "tests": c["tests"], **sc,
                     "intent": res.intent, "note": res.note,
                     "entries": [e.model_dump(mode="json") for e in res.entries],
                     "usage": ext.usage, "cached": ext.cached, "ms": int((time.monotonic() - t0) * 1000)})
        mark = "PASS" if sc["pass"] else "FAIL"
        print(f"  {c['id']} {mark}  {c['input'][:40]:<40} {'; '.join(sc['problems'])[:110]}")

    n = len(rows)
    passed = sum(r["pass"] for r in rows)
    fh = sum(r["field_hits"] for r in rows)
    ft = sum(r["field_total"] for r in rows)
    amb = [r for r in rows if r["kind"] in ("ambiguous", "intent")]
    clean = [r for r in rows if r["kind"] == "clean"]
    summary = {
        "model": settings.model, "run_at": datetime.now().isoformat(timespec="seconds"), "cases": n,
        "passed": passed, "field_accuracy": round(fh / ft, 3) if ft else None,
        "safety_rate": round(sum(r["safe"] for r in amb) / len(amb), 3) if amb else None,
        "false_alarm_rate": round(sum(r["false_alarm"] for r in clean) / len(clean), 3) if clean else None,
        "errors": sum(r["kind"] == "error" for r in rows),
        "api_calls": calls - cached, "cached": cached, "est_cost_usd": round(cost, 4),
    }

    print(f"\nPassed {passed}/{n}")
    print(f"Field accuracy (name/type/amount/date): {summary['field_accuracy']}")
    print(f"Safety (ambiguous inputs sent to review): {summary['safety_rate']}")
    print(f"False alarms (clean inputs flagged):      {summary['false_alarm_rate']}")
    print(f"Fresh API calls: {summary['api_calls']}  cached: {cached}  est. cost: ${summary['est_cost_usd']}")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))
    md = [f"# Kanakku eval — {summary['run_at']}", "", f"Model `{settings.model}`, {n} cases.", "",
          "| Metric | Value |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in summary.items() if k not in ("model", "run_at")]
    md += ["", "| Case | Result | Input | Problems |", "|---|---|---|---|"]
    md += [f"| {r['id']} | {'✅' if r['pass'] else '❌'} | `{r['input']}` | {'; '.join(r['problems'])} |" for r in rows]
    (out / f"{stamp}.md").write_text("\n".join(md))
    print(f"\nSaved eval/results/{stamp}.json and .md")


if __name__ == "__main__":
    main()
