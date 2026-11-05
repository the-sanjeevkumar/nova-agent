"""Tests for the email_* mailbox model tools.

Handlers run through the real registry dispatch path with fake IMAP/SMTP servers cut in
at ``adapter._connect_imap``/``_connect_smtp``: no live network, no real mailbox.
Contracts asserted: search composes server-side IMAP SEARCH keys and packages headers
newest-first without ever marking mail read; read honours BODY.PEEK plus an explicit
``\\Seen`` store instead of the implicit RFC822 side effect, reports cached attachment
paths and truncation; send keeps Bcc in the SMTP envelope only (never a header), skips
missing attachments by name, and turns a Sent-folder APPEND refusal into a result
warning while the send itself counts as success; the shared check_fn gates the family;
delegation children keep mailbox reads but can never carry email_send.
"""

from __future__ import annotations

import json
import re
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

# Registration side effect: importing the module registers the email tools.
import tools.email_tool
from tools import email_tool_runtime as runtime
from tools.registry import registry, invalidate_check_fn_cache

_IMAP_DATE_RE = re.compile(r"\d{2}-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{4}")


# --------------------------------------------------------------------------- fakes


class FakeImap:
    """The imaplib surface the runtime actually calls, plus command recording.

    ``messages`` maps plain-digit UIDs to RFC822 bytes; header-only fetches reuse the
    same bytes (the runtime parses requested headers out of them either way)."""

    capabilities: tuple = ()  # _send_imap_id skips servers not advertising the ID extension

    def __init__(self, messages: dict[str, bytes], *, select_ok: bool = True, append_ok: bool = True,
                 seen_uids: frozenset[str] | None = frozenset()):
        self._messages = messages
        self.select_ok = select_ok
        self.append_ok = append_ok
        self._seen = frozenset(seen_uids or ())  # messages the server reports as already read
        self.selected: list[str] = []
        self.searches: list[tuple] = []
        self.seen_stores: list[tuple[str, str]] = []
        self.appends: list[tuple[str, bytes]] = []

    def login(self, address, password) -> None:
        assert address and password, "runtime must authenticate before touching a mailbox"

    def select(self, mailbox: str):
        self.selected.append(mailbox)
        if not self.select_ok:
            return "NO", [b"[TRYCREATE] no such folder here"]
        return "OK", [f"mailbox {mailbox} selected".encode()]

    def uid(self, command: str, *args):
        if command == "search":
            self.searches.append(args)
            uids = " ".join(sorted(self._messages, key=int))
            return "OK", [uids.encode()] if uids else ("OK", [b""])
        if command == "store":
            self.seen_stores.append((args[0], args[2]))
            return "OK", [b"flag stored"]
        # fetch: the selection item is the parenthesised string argument.
        items = [a for a in args if isinstance(a, str) and a.startswith("(")]
        body_fetch = any("BODY.PEEK[]" in item or "RFC822" in item for item in items)
        uids = [str(a) for a in args if not (isinstance(a, str) and a.startswith("("))]
        results = []
        for uid in uids:
            payload = self._messages.get(uid)
            if payload is None:
                return "OK", []
            seen = "\\Seen" if uid in self._seen else ""  # the server decides the flag, not the reader
            marker = (f"(UID {uid} FLAGS ({seen}) BODY[] {{{len(payload)}}})" if body_fetch
                      else f"(UID {uid} FLAGS ({seen}) BODY[HEADER.FIELDS (...)] {{{len(payload)}}})")
            results.append((marker.encode(), payload, b")"))
        return "OK", results

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" INBOX',
                      b'(\\HasNoChildren) "/" Sent',
                      b'(\\HasNoChildren) "/" "Team Mailbox/Nursery"']

    def append(self, mailbox, flags, date, message: bytes):
        if not self.append_ok:
            return "NO", [b"folder does not exist"]
        self.appends.append((mailbox, message))
        return "OK", [b"appended"]

    def logout(self) -> None:  # _close_imap teardown touches exactly this
        pass


class FakeSmtp:
    """Records the login and envelope the runtime hands to smtplib."""

    def __init__(self, fail_login: bool = False):
        self.fail_login = fail_login
        self.logins: list[tuple] = []
        self.sends: list[tuple] = []
        self.quit = self.close = lambda: None

    def login(self, address, password):
        if self.fail_login:
            raise RuntimeError("authentication failed (bad password)")
        self.logins.append((address, password))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sends.append((from_addr, list(to_addrs or []), msg))


class FakeAdapter:
    """EmailAdapter stand-in: configured identity plus fake transports."""

    def __init__(self, imap: FakeImap, smtp: FakeSmtp | None = None):
        self._address = "agent@example.com"
        self._password = "app-password"
        self._imap = imap
        self._smtp = smtp

    def _connect_imap(self):
        return self._imap

    def _connect_smtp(self):
        assert self._smtp is not None, "send paths must reach SMTP"
        return self._smtp


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def mailbox_env(monkeypatch):
    """EMAIL_* set so the shared check_fn passes; the registry's probe cache invalidated."""
    for name, value in (("EMAIL_ADDRESS", "agent@example.com"), ("EMAIL_PASSWORD", "app-password"),
                        ("EMAIL_IMAP_HOST", "imap.example.com"), ("EMAIL_SMTP_HOST", "smtp.example.com")):
        monkeypatch.setenv(name, value)
    invalidate_check_fn_cache()
    yield
    invalidate_check_fn_cache()


@pytest.fixture
def adapter(monkeypatch, mailbox_env):
    """Runtime adapter resolution routed to the fakes each test passes in; returns them."""

    def install(imap: FakeImap, smtp: FakeSmtp | None = None) -> FakeAdapter:
        fake = FakeAdapter(imap, smtp)
        monkeypatch.setattr(runtime, "_require_adapter", lambda: fake)
        return fake

    return install


def dispatch(name: str, args: dict) -> dict:
    return json.loads(registry.dispatch(name, args))


def _simple_headers(uid: str) -> bytes:
    return (f"From: Ada <ada@friends.example>\r\nTo: agent@example.com\r\n"
            f"Subject: linen list {uid}\r\nDate: Fri, 02 Jan 2026 10:00:00 +0000\r\n"
            f"Message-ID: <m-{uid}@friends.example>\r\n").encode()


def _multipart_with_attachment() -> bytes:
    msg = MIMEMultipart()
    msg["From"] = "ada@friends.example"
    msg["Subject"] = "note and file"
    msg["Message-ID"] = "<m-full@friends.example>"
    msg.attach(MIMEText("Hello full body", "plain", "utf-8"))
    part = MIMEBase("application", "octet-stream")
    part.set_payload(b"attachment-bytes")
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename="note.txt")
    msg.attach(part)
    return msg.as_bytes()


# --------------------------------------------------------------------------- config gate


def test_check_fn_false_without_config(monkeypatch, mailbox_env):
    assert runtime.email_configured() is True  # env fixture passes the adapter's real probe
    for name in ("EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST"):
        monkeypatch.delenv(name, raising=False)
    assert runtime.email_configured() is False
    with pytest.raises(ValueError) as raised:
        runtime._require_adapter()
    assert "EMAIL_ADDRESS" in str(raised.value)  # the remedy names what to set


# --------------------------------------------------------------------------- search


def test_search_filters_newest_first_through_registry(adapter):
    imap = FakeImap({str(uid): _simple_headers(str(uid)) for uid in range(1, 25)})
    adapter(imap)
    result = dispatch("email_search", {"limit": 3, "sender": "ada", "unread_only": True})

    keys = " ".join(str(part) for part in imap.searches[0])
    assert "UNSEEN" in keys and 'FROM "ada"' in keys  # server-side filtering, quoted astring
    assert result["folder"] == "INBOX" and result["matched"] == 24 and result["returned"] == 3
    assert [message["uid"] for message in result["messages"]] == ["24", "23", "22"]  # newest-first
    assert result["messages"][0]["subject"] == "linen list 24"
    assert result["messages"][0]["from"] == "ada@friends.example"


def test_search_never_marks_mail_read(adapter):
    imap = FakeImap({"1": _simple_headers("1")})
    adapter(imap)
    result = dispatch("email_search", {})
    assert result["messages"][0]["seen"] is False and imap.seen_stores == []  # BODY.PEEK paths only


def test_search_since_translates_to_imap_date(adapter):
    imap = FakeImap({"3": _simple_headers("3")})
    adapter(imap)
    payload = dispatch("email_search", {"since": "10d"})
    keys = " ".join(str(part) for part in imap.searches[0])
    assert "SINCE" in keys and payload["matched"] >= 1
    assert _IMAP_DATE_RE.fullmatch(keys.split("SINCE ")[1])


def test_search_rejects_bad_since(adapter):
    adapter(FakeImap({}))
    result = dispatch("email_search", {"since": "yesterday"})
    assert "since must be" in result["error"] and "yesterday" in result["error"]


def test_search_surfaces_select_failures_actionably(adapter):
    adapter(FakeImap({}, select_ok=False))
    result = dispatch("email_search", {})
    assert "Selecting folder 'INBOX' failed" in result["error"]
    assert "email_folders" in result["error"]


# --------------------------------------------------------------------------- read


def test_read_returns_body_attachments_and_marks_seen(adapter):
    imap = FakeImap({"7": _multipart_with_attachment()})
    adapter(imap)
    result = dispatch("email_read", {"uid": "7"})

    assert result["subject"] == "note and file" and result["from"] == "ada@friends.example"
    assert result["body"] == "Hello full body" and result["body_truncated"] is False
    assert result["seen"] is True and result["message_id"] == "<m-full@friends.example>"
    assert result["attachments"][0]["path"].endswith("note.txt")
    assert [(uid, flags) for uid, flags in imap.seen_stores] == [("7", r"(\Seen)")]


def test_read_without_mark_read_leaves_flag(adapter):
    imap = FakeImap({"7": _multipart_with_attachment()})
    adapter(imap)
    result = dispatch("email_read", {"uid": "7", "mark_read": False})
    assert result["seen"] is False and imap.seen_stores == []


def test_read_requires_numeric_uid_and_fails_clearly_on_miss(adapter):
    adapter(FakeImap({"1": _simple_headers("1")}))
    injection = dispatch("email_read", {"uid": "1 +FLAGS (\\Deleted)"})
    assert "numeric UID" in injection["error"]
    missing = dispatch("email_read", {"uid": "99"})
    assert "email_search" in missing["error"]  # recovery points back at the search tool


def test_read_caps_long_bodies_with_continuation_hint(adapter):
    long_body = "X" * (runtime.READ_BODY_CHARS_MAX + 1)
    message = MIMEText(long_body, "plain", "utf-8")
    message["From"] = "ada@friends.example"
    message["Message-ID"] = "<long@friends.example>"
    adapter(FakeImap({"1": message.as_bytes()}))
    result = dispatch("email_read", {"uid": "1", "body_chars": 500})
    assert result["body_truncated"] is True and len(result["body"]) < 600
    assert str(runtime.READ_BODY_CHARS_MAX) in result["body"]


# --------------------------------------------------------------------------- folders


def test_folders_lists_server_names(adapter):
    adapter(FakeImap({}))
    result = dispatch("email_folders", {})
    names = [folder["name"] for folder in result["folders"]]
    assert names == ["INBOX", "Sent", "Team Mailbox/Nursery"]  # quotes stripped, spelling intact
    assert all(folder["delimiter"] == "/" for folder in result["folders"])


# --------------------------------------------------------------------------- send


def test_send_smtp_envelope_carries_bcc_without_header(adapter):
    imap, smtp = FakeImap({}), FakeSmtp()
    adapter(imap, smtp)
    result = dispatch("email_send", {"to": "ada@friends.example", "cc": "bob@friends.example",
                                     "bcc": "hidden@friends.example", "subject": "progress",
                                     "body": "Here is the report body."})

    assert result["success"] is True
    assert result["from"] == "agent@example.com" and result["bcc_count"] == 1
    from_addr, envelope, msg = smtp.sends[0]
    assert from_addr == "agent@example.com"
    assert envelope == ["ada@friends.example", "bob@friends.example", "hidden@friends.example"]
    assert "Bcc:" not in msg.as_string()  # the hidden copy lives only in the envelope
    assert smtp.logins == [("agent@example.com", "app-password")]


def test_send_skips_missing_attachments_and_reports(adapter, tmp_path):
    existing = tmp_path / "report.txt"
    existing.write_text("data", encoding="utf-8")
    adapter(FakeImap({}), FakeSmtp())
    result = dispatch("email_send", {"to": "ada@friends.example", "body": "body",
                                     "attachments": [str(tmp_path / "gone.bin"), str(existing)]})
    assert result["attachments_attached"] == ["report.txt"]
    assert result["attachments_skipped"] == [str(tmp_path / "gone.bin")]


def test_send_saves_sent_copy_and_reports_folder_failures(adapter):
    imap, smtp = FakeImap({}), FakeSmtp()
    adapter(imap, smtp)
    ok = dispatch("email_send", {"to": "ada@friends.example", "body": "body", "save_to_sent": True})
    assert ok["success"] is True and ok["saved_to_sent"] is True
    assert imap.appends[0][0] == "Sent" and imap.appends[0][1]  # the RFC822 copy, not text

    imap.append_ok = False
    refused = dispatch("email_send", {"to": "ada@friends.example", "body": "body", "save_to_sent": True})
    assert refused["success"] is True  # the message itself already landed
    assert refused["saved_to_sent"] is False and "saving to 'Sent' failed" in refused["warnings"][0]


def test_send_validates_recipients_and_body(adapter):
    adapter(FakeImap({}), FakeSmtp())
    no_body = dispatch("email_send", {"to": "ada@friends.example", "body": "   "})
    assert "body is required" in no_body["error"]
    bad_to = dispatch("email_send", {"to": "not an address", "body": "hi"})
    assert "to requires at least one valid email address" in bad_to["error"]


def test_send_login_failure_surfaces_adapter_error_text(adapter):
    adapter(FakeImap({}), FakeSmtp(fail_login=True))
    result = dispatch("email_send", {"to": "ada@friends.example", "body": "body"})
    assert "authentication failed (bad password)" in result["error"]


# --------------------------------------------------------------------------- role boundary


def test_children_keep_mailbox_reads_but_never_email_send():
    from tools.delegate_tool_toolsets import DELEGATE_BLOCKED_TOOLS, _blocked_toolsets_for_role
    assert "email_send" in DELEGATE_BLOCKED_TOOLS
    assert "email_send" in _blocked_toolsets_for_role("leaf")
    assert "email" not in _blocked_toolsets_for_role("leaf")  # mailbox reads survive the deny
