import importlib.util, pathlib
from app import auth
from app.db import Database

spec = importlib.util.spec_from_file_location("manage_user", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "manage_user.py")
mu = importlib.util.module_from_spec(spec); spec.loader.exec_module(mu)


def _db(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'t.db'}"); db.init_schema(); return db


def test_create_then_reset(tmp_path):
    db = _db(tmp_path)
    assert mu.main(["New@Example.com"], db=db, password="firstpass1") == 0
    assert auth.authenticate(db, "new@example.com", "firstpass1")["role"] == "admin"
    assert mu.main(["new@example.com"], db=db, password="secondpass2") == 0
    assert auth.authenticate(db, "new@example.com", "firstpass1") is None
    assert auth.authenticate(db, "new@example.com", "secondpass2")
    assert db.one("SELECT COUNT(*) n FROM users")["n"] == 1


def test_short_password_and_other_users_untouched(tmp_path):
    db = _db(tmp_path)
    auth.create_user(db, "a@x.com", "keepme123", "admin")
    assert mu.main(["b@x.com"], db=db, password="short") == 1
    assert mu.main(["b@x.com", "--role", "user"], db=db, password="longenough1") == 0
    assert auth.authenticate(db, "a@x.com", "keepme123")
