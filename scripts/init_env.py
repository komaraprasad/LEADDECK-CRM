"""Create a local .env with a random SECRET_KEY and a random admin password (printed once). Safe to re-run: never overwrites."""
import pathlib, secrets

p = pathlib.Path(__file__).resolve().parent.parent / ".env"
if p.exists():
    print(".env already exists - leaving it unchanged.")
else:
    pw = secrets.token_urlsafe(9)
    p.write_text(f"SECRET_KEY={secrets.token_hex(32)}\nADMIN_EMAIL=admin@leaddeck.local\nADMIN_PASSWORD={pw}\nDATABASE_URL=sqlite:///leaddeck.db\nSYNC_TOKEN=\n", encoding="utf-8")
    print("=" * 60)
    print("Created .env for LOCAL use.")
    print("  Login email   : admin@leaddeck.local")
    print(f"  Login password: {pw}")
    print("Write it down now. It is stored only in .env (never commit that file).")
    print("=" * 60)
