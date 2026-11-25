"""Create a LeadDeck user, or reset a user's password, directly in the local database.

Run from the project folder:   python scripts/manage_user.py you@company.com
  - If the email exists, you are asked for a new password (reset it).
  - If it does not exist, the account is created (add --role user for a non-admin).
The password is typed hidden and is never printed or stored in plain text (scrypt hash only).
Nothing else in the database is touched.
"""
from __future__ import annotations
import argparse
import getpass
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auth                      # noqa: E402
from app.config import load_dotenv, Settings  # noqa: E402
from app.db import Database              # noqa: E402
import os                                 # noqa: E402


def main(argv=None, db: Database | None = None, password: str | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email")
    ap.add_argument("--role", choices=("admin", "user"), default="admin", help="role when creating (default admin)")
    a = ap.parse_args(argv)
    email = a.email.strip().lower()

    if db is None:
        load_dotenv()
        db = Database(os.environ.get("DATABASE_URL") or Settings().database_url)
        db.init_schema()
    if password is None:
        password = getpass.getpass("New password (min 8 characters, hidden): ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match. Nothing changed.")
            return 1
    if len(password) < 8:
        print("Password must be at least 8 characters. Nothing changed.")
        return 1

    existing = db.one("SELECT id FROM users WHERE email=?", (email,))
    if existing:
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(password), existing["id"]))
        print(f"Password reset for {email}. You can sign in now.")
    else:
        try:
            auth.create_user(db, email, password, a.role)
        except ValueError as e:
            print(f"Could not create the user: {e}")
            return 1
        print(f"Created {a.role} account {email}. You can sign in now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
