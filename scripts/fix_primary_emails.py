"""One-off repair: re-choose each enriched lead's primary email with the current rules
(valid first, then provider grade, then company domain > other work domain > personal mailbox).
Run from the project folder:  .venv\\Scripts\\python.exe scripts\\fix_primary_emails.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import load_dotenv  # noqa: E402

load_dotenv()
from app.db import Database  # noqa: E402
from app.enrich import repick_primary_emails  # noqa: E402

db = Database(os.environ.get("DATABASE_URL", "sqlite:///leaddeck.db"))
ids = repick_primary_emails(db)
for pid in ids:
    p = db.one("SELECT full_name, email FROM people WHERE id=?", (pid,))
    print(f"fixed #{pid} {p['full_name']}: primary email now {p['email']}")
print(f"{len(ids)} lead(s) changed")
