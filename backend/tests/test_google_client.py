import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.google.client import (
    REQUIRED_SCOPES, GoogleAuthError, GoogleConflict, GoogleError, GoogleHistoryGone, GoogleNotFound,
    GoogleTransientError,
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


@pytest.mark.parametrize("body", [
    {"error": {"code": 403, "errors": [{"domain": "usageLimits", "reason": "rateLimitExceeded"}]}},
    {"error": {"code": 403, "errors": [{"domain": "usageLimits", "reason": "userRateLimitExceeded"}]}},
    {"error": {"code": 403, "errors": [{"domain": "global", "reason": "backendError"}]}},
    {"error": {"code": 403, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}},
    {"error": {"code": 403, "errors": [{"reason": "forbidden"}, {"reason": "userRateLimitExceeded"}]}},
])
def test_403_rate_limit_is_transient(body):
    c = client_with(lambda req: httpx.Response(403, json=body))
    with pytest.raises(GoogleTransientError):
        c.gmail_get_raw("tok", "m1")


@pytest.mark.parametrize("resp", [
    httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                        "errors": [{"reason": "insufficientPermissions"}]}}),
    httpx.Response(403, json={"error": {"code": 403, "errors": [{"reason": "forbidden"}]}}),
    httpx.Response(403, json={"error": "access_denied"}),
    httpx.Response(403, json={"error": {"errors": "rateLimitExceeded"}}),
    httpx.Response(403, json=["rateLimitExceeded"]),
    httpx.Response(403, json={"error": {"errors": [{"reason": ["rateLimitExceeded"]}]}}),
    httpx.Response(403, text="<html>rateLimitExceeded</html>"),
])
def test_403_other_is_plain_google_error(resp):
    c = client_with(lambda req: resp)
    with pytest.raises(GoogleError) as ei:
        c.gmail_get_raw("tok", "m1")
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


# --- Calendar ---
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def test_calendar_insert_request_shape():
    seen = {}

    def handler(req):
        seen["method"], seen["url"] = req.method, str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "fuag1", "htmlLink": "https://cal/x", "status": "confirmed"})

    body = {"id": "fuag1", "summary": "[업무] 테스트"}
    ev = client_with(handler).calendar_insert("tok", body)
    assert (seen["method"], seen["url"]) == ("POST", CAL)
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == body
    assert (ev.id, ev.html_link, ev.status) == ("fuag1", "https://cal/x", "confirmed")


def test_calendar_patch_and_get():
    seen = []

    def handler(req):
        seen.append((req.method, str(req.url), req.content))
        return httpx.Response(200, json={"id": "e1", "status": "tentative"})

    c = client_with(handler)
    g = c.calendar_get("tok", "e1")
    p = c.calendar_patch("tok", "e1", {"summary": "x"})
    assert seen[0][:2] == ("GET", f"{CAL}/e1")
    assert seen[1][:2] == ("PATCH", f"{CAL}/e1")
    assert json.loads(seen[1][2]) == {"summary": "x"}
    assert g.status == "tentative" and g.html_link == ""
    assert p.id == "e1"


@pytest.mark.parametrize("status", [404, 410])
def test_calendar_404_410_not_found(status):
    c = client_with(lambda req: httpx.Response(status, json={"error": {"message": "gone"}}))
    with pytest.raises(GoogleNotFound):
        c.calendar_get("tok", "e1")
    with pytest.raises(GoogleNotFound):
        c.calendar_patch("tok", "e1", {})


def test_calendar_409_conflict():
    c = client_with(lambda req: httpx.Response(409, json={"error": {"message": "dup"}}))
    with pytest.raises(GoogleConflict):
        c.calendar_insert("tok", {"id": "e1"})


def test_calendar_403_rate_limit_transient():
    body = {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
    c = client_with(lambda req: httpx.Response(403, json=body))
    with pytest.raises(GoogleTransientError):
        c.calendar_get("tok", "e1")


def test_calendar_error_message_has_no_secrets():
    c = client_with(lambda req: httpx.Response(409, json={"error": {"message": "SECRETBODY"}}))
    with pytest.raises(GoogleConflict) as ei:
        c.calendar_insert("tok-secret", {"summary": "SECRETBODY"})
    assert "SECRET" not in str(ei.value) and "tok-secret" not in str(ei.value)


def test_gmail_history_404_still_history_gone_and_get_raw_404_is_google_error():
    c = client_with(lambda req: httpx.Response(404, json={}))
    with pytest.raises(GoogleHistoryGone):
        c.gmail_history("tok", "1", None)
    with pytest.raises(GoogleError):
        c.gmail_get_raw("tok", "m1")


def test_fake_deleted_event_semantics():
    f = FakeGoogle()
    f.calendar_insert("t", {"id": "e1", "summary": "a"})
    f.delete_event("e1")
    with pytest.raises(GoogleConflict):
        f.calendar_insert("t", {"id": "e1"})
    assert f.calendar_get("t", "e1").status == "cancelled"
    with pytest.raises(GoogleNotFound):
        f.calendar_patch("t", "e1", {"summary": "b"})
