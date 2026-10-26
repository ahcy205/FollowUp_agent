import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.google.client import (
    REQUIRED_SCOPES, GoogleAuthError, GoogleError, GoogleHistoryGone, GoogleTransientError,
    HttpGoogleClient, get_google_client, set_google_override,
)
from tests.fake_google import FakeGoogle, make_raw


def client_with(handler) -> HttpGoogleClient:
    return HttpGoogleClient(transport=httpx.MockTransport(handler))


def test_authorization_url_params():
    c = HttpGoogleClient()
    url = c.authorization_url(state="st", code_challenge="ch", redirect_uri="http://x/cb")
    u = urlparse(url)
    assert f"{u.scheme}://{u.netloc}{u.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert q["access_type"] == "offline"
    assert q["prompt"] == "consent"
    assert q["include_granted_scopes"] == "true"
    assert q["code_challenge_method"] == "S256"
    assert q["code_challenge"] == "ch"
    assert q["response_type"] == "code"
    assert q["state"] == "st"
    assert q["redirect_uri"] == "http://x/cb"
    assert q["scope"] == " ".join(REQUIRED_SCOPES)


def test_exchange_code_parses_scopes():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = parse_qs(req.content.decode())
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3599,
                                         "scope": "a b c"})

    g = client_with(handler).exchange_code(code="c", code_verifier="v", redirect_uri="http://x/cb")
    assert g.scopes == ["a", "b", "c"] and g.refresh_token == "r" and g.expires_in == 3599
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert seen["body"]["grant_type"] == ["authorization_code"]
    assert seen["body"]["code_verifier"] == ["v"]


def test_refresh_invalid_grant_raises_auth_error():
    c = client_with(lambda req: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(GoogleAuthError):
        c.refresh_access_token("r")


def test_refresh_ok():
    c = client_with(lambda req: httpx.Response(200, json={"access_token": "new", "expires_in": 3600}))
    t = c.refresh_access_token("r")
    assert t.token == "new" and t.expires_at.tzinfo is not None


def test_401_raises_auth_error():
    c = client_with(lambda req: httpx.Response(401, json={"error": {"code": 401}}))
    with pytest.raises(GoogleAuthError):
        c.gmail_profile("tok")


def test_history_404_raises_gone():
    c = client_with(lambda req: httpx.Response(404, json={"error": {"code": 404}}))
    with pytest.raises(GoogleHistoryGone):
        c.gmail_history("tok", "1", None)


def test_other_4xx_is_google_error():
    c = client_with(lambda req: httpx.Response(403, json={}))
    with pytest.raises(GoogleError) as ei:
        c.gmail_profile("tok")
    assert not isinstance(ei.value, (GoogleAuthError, GoogleTransientError))


@pytest.mark.parametrize("status", [429, 503])
def test_429_and_503_raise_transient(status):
    c = client_with(lambda req: httpx.Response(status))
    with pytest.raises(GoogleTransientError):
        c.gmail_profile("tok")


def test_network_error_raises_transient():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(GoogleTransientError):
        client_with(handler).gmail_list("tok", "q", None)


def test_get_raw_decodes_base64url():
    data = b"Subject: hi\r\n\r\n\xed\x95\x9c\xea\xb8\x80??>>"
    enc = base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    seen = {}

    def handler(req):
        seen["url"] = req.url
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"id": "m1", "threadId": "t1", "labelIds": ["INBOX"],
                                         "internalDate": "1700000000000", "raw": enc})

    m = client_with(handler).gmail_get_raw("tok", "m1")
    assert m.raw == data and m.id == "m1" and m.thread_id == "t1"
    assert m.internal_date_ms == 1700000000000 and m.label_ids == ["INBOX"]
    assert seen["url"].path == "/gmail/v1/users/me/messages/m1"
    assert seen["url"].params["format"] == "raw"
    assert seen["auth"] == "Bearer tok"


def test_history_collects_messages_added():
    seen = {}

    def handler(req):
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"historyId": "2000", "history": [
            {"messagesAdded": [{"message": {"id": "a"}}, {"message": {"id": "b"}}]},
            {"messagesAdded": [{"message": {"id": "a"}}]},
            {"labelsAdded": []},
        ]})

    p = client_with(handler).gmail_history("tok", "1500", "pg")
    assert p.message_ids == ["a", "b"] and p.history_id == "2000" and p.next_page_token is None
    assert seen["params"]["historyTypes"] == "messageAdded"
    assert seen["params"]["startHistoryId"] == "1500"
    assert seen["params"]["pageToken"] == "pg"


def test_list_and_profile():
    def handler(req):
        if req.url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "me@x.com", "historyId": "77"})
        assert req.url.params["q"] == "after:5"
        return httpx.Response(200, json={"messages": [{"id": "a"}], "nextPageToken": "n"})

    c = client_with(handler)
    pr = c.gmail_profile("t")
    assert pr.email == "me@x.com" and pr.history_id == "77"
    p = c.gmail_list("t", "after:5", None)
    assert p.message_ids == ["a"] and p.next_page_token == "n"


def test_revoke_posts_token():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = parse_qs(req.content.decode())
        return httpx.Response(200)

    client_with(handler).revoke("tok")
    assert seen["url"] == "https://oauth2.googleapis.com/revoke" and seen["body"]["token"] == ["tok"]


def test_override():
    fake = FakeGoogle()
    set_google_override(fake)
    try:
        assert get_google_client() is fake
    finally:
        set_google_override(None)
    assert get_google_client() is get_google_client()


def test_fake_google_behaviour():
    f = FakeGoogle()
    m1 = f.deliver(make_raw(sender="a@x.com", to="me@gmail.com", subject="s"))
    start = str(f.history_id)
    m2 = f.deliver(make_raw(sender="b@x.com", to="me@gmail.com", subject="s2"), internal_date_ms=500)
    assert f.gmail_history("t", start, None).message_ids == [m2]
    assert f.gmail_list("t", "after:1 -in:spam", None).message_ids == [m1]
    assert f.gmail_get_raw("t", m1).thread_id == m1 and f.get_calls == [m1]
    with pytest.raises(GoogleError):
        f.gmail_get_raw("t", "nope")
    f.history_gone = True
    with pytest.raises(GoogleHistoryGone):
        f.gmail_history("t", start, None)
    g = f.exchange_code(code="c", code_verifier="v", redirect_uri="r")
    assert f.refresh_access_token(g.refresh_token).token.startswith("at-")
    with pytest.raises(GoogleAuthError):
        f.refresh_access_token("bad")
    f.revoke("x")
    assert f.revoked == ["x"]
    f.revoke_fails = True
    with pytest.raises(GoogleTransientError):
        f.revoke("y")


def test_fake_list_paginates():
    f = FakeGoogle()
    ids = [f.deliver(make_raw(sender="a@x.com", to="m@x.com", subject=str(i))) for i in range(5)]
    got, tok = [], None
    while True:
        p = f.gmail_list("t", "", tok, page_size=2)
        got += p.message_ids
        tok = p.next_page_token
        if not tok:
            break
    assert sorted(got) == sorted(ids)
