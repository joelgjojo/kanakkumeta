"""Send one ledger photo to the model and print what it read.

    python scripts/try_image.py path/to/page1.jpg
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import extractor  # noqa: E402
from app.ledger import Ledger  # noqa: E402
from app.pipeline import build_proposal  # noqa: E402

if len(sys.argv) < 2:
    sys.exit("usage: python scripts/try_image.py photo.jpg [hint]")
path = Path(sys.argv[1])
hint = sys.argv[2] if len(sys.argv) > 2 else ""
ext = extractor.extract_image(path.read_bytes(), extractor.guess_mime(str(path)), date.today().isoformat(), hint)
res = build_proposal(ext, ledger=Ledger(":memory:"), today=date.today(), source_type="image",
                     source_ref=path.name, raw_input=hint or None)

print(f"intent={res.intent}  cached={ext.cached}  usage={ext.usage}")
if res.note:
    print("note:", res.note)
for e in res.entries:
    flags = ", ".join(f.value for f in e.flags if f.value != "new_customer")
    print(f"  #{e.index + 1} [{e.status:12}] {e.customer_name!s:12} {e.transaction_type.value if e.transaction_type else None!s:12}"
          f" {e.amount!s:>8}  {e.transaction_date}  read={e.evidence!r}  {('FLAGS: ' + flags) if flags else ''}")
Path("last_image_result.json").write_text(json.dumps(res.model_dump(mode="json"), ensure_ascii=False, indent=2))
print("\nFull result saved to last_image_result.json")
