import base64
from datetime import timezone

from app.mail.parse import html_to_text, parse_raw, strip_quoted
from tests.fake_google import make_raw


def _raw(**kw):
    kw.setdefault("sender", "홍길동 <Hong@Acme.co.kr>")
    kw.setdefault("to", "me@gmail.com")
    kw.setdefault("subject", "견적 문의")
    return make_raw(**kw)


def test_plain_utf8():
    p = parse_raw(_raw(body="안녕하세요.\n견적 부탁드립니다."))
    assert p.sender_email == "hong@acme.co.kr"
    assert p.sender_name == "홍길동"
    assert p.subject == "견적 문의"
    assert p.text == "안녕하세요.\n견적 부탁드립니다."


def test_prefers_plain_over_html():
    p = parse_raw(_raw(body="평문 본문", html="<p>HTML 본문</p>"))
    assert p.text == "평문 본문"


def test_html_only_converted():
    p = parse_raw(_raw(html="<p>안녕하세요<br>견적 부탁드립니다</p><script>x</script>"))
    assert p.text.splitlines() == ["안녕하세요", "견적 부탁드립니다"]
    assert "x" not in p.text


def test_html_to_text_skips_style_and_unescapes():
    t = html_to_text("<style>p{}</style><div>a &amp; b</div><ul><li>one</li><li>two</li></ul>")
    assert t.splitlines() == ["a & b", "one", "two"]


def test_ks_c_5601_body():
    body = "안녕하세요, 견적 부탁드립니다."
    b64 = base64.b64encode(body.encode("cp949")).decode()
    raw = (
        "From: =?ks_c_5601-1987?B?" + base64.b64encode("홍길동".encode("cp949")).decode() + "?= <hong@acme.co.kr>\r\n"
        "To: me@gmail.com\r\nSubject: test\r\nMIME-Version: 1.0\r\n"
        'Content-Type: text/plain; charset="ks_c_5601-1987"\r\n'
        "Content-Transfer-Encoding: base64\r\n\r\n" + b64 + "\r\n"
    ).encode("ascii")
    p = parse_raw(raw)
    assert p.text == body
    assert p.sender_name == "홍길동"


def test_euc_kr_body_with_extended_char():
    body = "똠방각하 확인"  # 똠 is cp949-only
    raw = (
        "From: a@b.com\r\nTo: me@gmail.com\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
        'Content-Type: text/plain; charset="euc-kr"\r\nContent-Transfer-Encoding: base64\r\n\r\n'
        + base64.b64encode(body.encode("cp949")).decode() + "\r\n"
    ).encode("ascii")
    assert parse_raw(raw).text == body


def test_unknown_charset_does_not_raise():
    raw = (b"From: a@b.com\r\nTo: me@gmail.com\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
           b'Content-Type: text/plain; charset="x-nonsense"\r\n\r\nhello\r\n')
    assert parse_raw(raw).text == "hello"


def test_encoded_word_subject():
    raw = (b"From: a@b.com\r\nTo: me@gmail.com\r\nSubject: =?UTF-8?B?"
           + base64.b64encode("견적서 보내드립니다".encode()) + b"?=\r\n\r\nbody\r\n")
    assert parse_raw(raw).subject == "견적서 보내드립니다"


def test_long_subject_truncated():
    assert len(parse_raw(_raw(subject="가" * 400, body="x")).subject) == 300


def test_attachment_only():
    p = parse_raw(_raw(body="", attachments=[("견적서.pdf", b"%PDF-1.4", "application/pdf")]))
    assert p.text == ""
    assert p.attachments == ["견적서.pdf"]


def test_missing_headers_and_garbage_date():
    p = parse_raw(b"Subject: hi\r\nDate: not a date\r\n\r\nbody")
    assert p.sender_email == "" and p.sender_name == ""
    assert p.to == [] and p.cc == []
    assert p.sent_at is None
    assert p.rfc_message_id is None and p.in_reply_to is None
    assert p.references == [] and p.precedence == "" and p.list_unsubscribe is False
    assert p.text == "body"
    parse_raw(b"")


def test_sent_at_utc():
    p = parse_raw(_raw(body="x", headers={"Date": "Fri, 10 Oct 2026 15:00:00 +0900"}))
    assert p.sent_at.tzinfo == timezone.utc
    assert (p.sent_at.hour, p.sent_at.day) == (6, 10)


def test_strip_gmail_korean_quote():
    t = "확인했습니다.\n\n2026년 10월 10일 (금) 오후 3:00, 김 <k@x.com>님이 작성:\n> 이전"
    assert strip_quoted(t) == "확인했습니다."


def test_strip_english_quote():
    t = "Thanks\n\nOn Fri, Oct 10, 2026 at 3:00 PM Kim <k@x.com> wrote:\n> old\n> older"
    assert strip_quoted(t) == "Thanks"


def test_strip_outlook_original():
    assert strip_quoted("OK\n-----Original Message-----\nFrom: a\nold") == "OK"
    assert strip_quoted("네\n\n-----원본 메시지-----\n보낸 사람: a\nold") == "네"


def test_strip_outlook_from_block():
    assert strip_quoted("Yes\n\nFrom: Kim <k@x.com>\nSent: Monday\nTo: me\nold") == "Yes"
    assert strip_quoted("From: me about this\nnot a header block") == "From: me about this\nnot a header block"


def test_strip_gt_lines():
    assert strip_quoted("a\n> quoted\nb\n>> more") == "a\nb"


def test_headers():
    p = parse_raw(_raw(body="x", message_id="<m1@acme>", headers={
        "In-Reply-To": "<m0@acme>", "References": "<m-1@acme> <m0@acme>",
        "List-Unsubscribe": "<mailto:u@acme>", "Precedence": "Bulk"}))
    assert p.rfc_message_id == "<m1@acme>"
    assert p.in_reply_to == "<m0@acme>"
    assert p.references == ["<m-1@acme>", "<m0@acme>"]
    assert p.list_unsubscribe is True
    assert p.precedence == "bulk"


def test_cc_and_multiple_to():
    p = parse_raw(_raw(to="A <a@x.com>, B@Y.com", cc="c@z.com, D <d@z.com>", body="x"))
    assert p.to == ["a@x.com", "b@y.com"]
    assert p.cc == ["c@z.com", "d@z.com"]


def test_body_capped():
    assert len(parse_raw(_raw(body="가" * 30000)).text) == 20000


def _b64word(s: str) -> str:
    return "=?utf-8?b?" + base64.b64encode(s.encode()).decode() + "?="


def test_nul_characters_stripped():
    """PostgreSQL rejects NUL in text columns; a stray \x00 must never block storing a mail."""
    body = base64.b64encode("본문\x00내용\x00".encode()).decode()
    att = base64.b64encode(b"%PDF").decode()
    raw = (
        f"From: {_b64word('홍\x00길동')} <hong@acme.co.kr>\r\n"
        "To: me@gmail.com\r\n"
        f"Subject: {_b64word('견적\x00문의')}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n"
        f"{body}\r\n"
        "--B\r\nContent-Type: application/pdf\r\nContent-Transfer-Encoding: base64\r\n"
        f'Content-Disposition: attachment; filename="{_b64word("견적\x00서.pdf")}"\r\n\r\n'
        f"{att}\r\n--B--\r\n"
    ).encode()
    p = parse_raw(raw)
    assert p.subject == "견적문의"
    assert p.text == "본문내용"
    assert p.sender_name == "홍길동"
    assert p.attachments == ["견적서.pdf"]
    for v in (p.subject, p.text, p.sender_name, *p.attachments):
        assert "\x00" not in v
