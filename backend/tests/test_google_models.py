import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Email, GoogleAccount, GoogleAccountStatus, User
from tests.conftest import register


def _user(db, email: str) -> User:
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    return u


def _email(user_id: int, gmail_id: str | None) -> Email:
    return Email(
        user_id=user_id, sender="a@x.com", receiver="b@x.com", subject="s", content="c",
        direction="INBOUND", gmail_message_id=gmail_id,
    )


def test_gmail_message_id_unique_per_user():
    with SessionLocal() as db:
        u = _user(db, "a@example.com")
        db.add(_email(u.id, "g1"))
        db.commit()
        db.add(_email(u.id, "g1"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_same_gmail_id_other_user_ok():
    with SessionLocal() as db:
        u1 = _user(db, "a@example.com")
        u2 = _user(db, "b@example.com")
        db.add(_email(u1.id, "g1"))
        db.add(_email(u2.id, "g1"))
        db.commit()


def test_seed_emails_have_null_gmail_fields(client):
    register(client)
    with SessionLocal() as db:
        emails = db.scalars(select(Email)).all()
        assert emails
        for e in emails:
            assert e.gmail_message_id is None
            assert e.triage is None
            assert e.cc == [] and e.attachments == [] and e.gmail_meta == {}


def test_one_google_account_per_user():
    with SessionLocal() as db:
        u = _user(db, "a@example.com")
        kw = dict(user_id=u.id, google_email="a@gmail.com", refresh_token_enc="enc",
                  scopes=["openid"], status=GoogleAccountStatus.CONNECTED)
        db.add(GoogleAccount(**kw))
        db.commit()
        db.add(GoogleAccount(**kw))
        with pytest.raises(IntegrityError):
            db.commit()
