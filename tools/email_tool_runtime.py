"""Mailbox plumbing behind the ``email_*`` model tools (search/read/send/folders).

Runs the agent's own configured mailbox — the SAME EMAIL_* configuration the gateway's
email channel uses — so the agent can work the inbox itself instead of only reacting to
authorized messages routed through the gateway. Connection, TLS, parsing and attachment
handling come from ``plugins.platforms.email.adapter`` (constructed with the
``platforms.email`` YAML section as extra, so config.yaml-only setups resolve exactly
like the gateway adapter); the tool layer (``email_tool.py``) only builds schemas and
stringify results.

Everything here is blocking; handlers run it on a worker thread via ``asyncio.to_thread``
(copied context keeps the profile's secret scope across the hop).
"""

from __future__ import annotations

import email as email_lib
import logging
import re
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from email.header import Header
from email.parser import BytesParser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, getaddresses
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# email_search returns headers only (bodies live behind email_read), so a page covers
# routine triage; bigger figures cost model time without adding coverage.
SEARCH_DEFAULT = 20
SEARCH_MAX = 50
READ_BODY_CHARS_DEFAULT = 20_000
READ_BODY_CHARS_MAX = 50_000
# IMAP SEARCH SINCE month names are always ASCII (RFC 3501); strftime("%b") follows the
# process locale, which localizes month abbreviations on non-English hosts.
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# One IMAP LIST response line: flags, hierarchy delimiter, mailbox (atom, or quoted with spaces).
_FOLDER_LINE_RE = re.compile(r'^\(\s*(?P<flags>[^)]*)\s*\)\s+"(?P<sep>[^"]*)"\s+(?P<mbox>.+)$')
_HEADER_FIELDS = ("FROM", "TO", "CC", "SUBJECT", "DATE", "MESSAGE-ID")
_FETCH_HEADERS = "(FLAGS BODY.PEEK[HEADER.FIELDS (" + " ".join(_HEADER_FIELDS) + ")])"


def _imap_family(status, detail) -> str:
    """One readable line from an IMAP NO/BAD response tuple the way retries would log it."""
    return b" ".join(p for p in (detail or ()) if isinstance(p, bytes)).decode("utf-8", "replace").strip()


def _yaml_email_extra() -> dict[str, Any]:
    """The profile's ``platforms.email`` config.yaml section (empty dict when absent).

    The adapter reads EMAIL_* secrets FIRST and falls back to this extra, mirroring how
    the gateway builds a PlatformConfig from both sources.
    """
    try:
        from nova_cli.config import load_config
        section = (load_config().get("platforms", {}) or {}).get("email", {}) or {}
        return dict(section) if isinstance(section, dict) else {}
    except Exception:  # load_config needs a readable profile; an unreadable one = unconfigured
        return {}


def _password_configured() -> bool:
    from gateway.platforms._shared import get_scoped_secret
    return bool(str(get_scoped_secret("EMAIL_PASSWORD", "") or "").strip())


def email_configured() -> bool:
    """True when a mailbox this profile can use exists: EMAIL_* secrets (the adapter's own
    check, blank-values-count-as-missing included) or YAML hosts plus a profile password."""
    try:
        from plugins.platforms.email.adapter import check_email_requirements
        if check_email_requirements():
            return True
    except Exception:  # check_fn must be a pure bool probe; in-tree this import cannot fail
        pass
    extra = _yaml_email_extra()
    return (bool(str(extra.get("address", "")).strip()) and bool(str(extra.get("imap_host", "")).strip())
            and bool(str(extra.get("smtp_host", "")).strip()) and _password_configured())


def _require_adapter():
    """Fresh EmailAdapter for one tool call, or a setup error the model can act on."""
    if not email_configured():
        raise ValueError(
            "No email mailbox is configured for this profile. Set EMAIL_ADDRESS, EMAIL_PASSWORD, "
            "EMAIL_IMAP_HOST and EMAIL_SMTP_HOST (via `nova gateway setup`, the dashboard email "
            "page, or .env), or `platforms.email` in config.yaml.")
    from gateway.config import PlatformConfig
    from plugins.platforms.email.adapter import EmailAdapter
    return EmailAdapter(PlatformConfig(enabled=False, extra=_yaml_email_extra()))


@contextmanager
def _open_mailbox(adapter, folder: str) -> Iterator:
    """Authenticated IMAP handle with *folder* selected: the adapter's INBOX handle wired to
    an arbitrary mailbox, so search/read work in Sent, Archive, any label."""
    from plugins.platforms.email.adapter import _close_imap, _send_imap_id
    imap = adapter._connect_imap()
    try:
        imap.login(adapter._address, adapter._password)
        _send_imap_id(imap)
        mailbox = f'"{folder}"' if " " in folder and not folder.startswith('"') else folder
        status, detail = imap.select(mailbox)
        if status != "OK":
            raise ValueError(f"Selecting folder {folder!r} failed ({_imap_family(status, detail)}). "
                             "Run email_folders for the exact folder names this server accepts.")
        yield imap
    finally:
        _close_imap(imap)


def _search_string(value: str) -> str:
    """IMAP astring: quoted, with the two specials escaped so a stray quote cannot
    terminate the search clause early."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _search_since(value: str) -> str:
    """``YYYY-MM-DD`` or ``Nd`` (days back) rendered as an IMAP SEARCH SINCE date, or ''."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if match := re.fullmatch(r"(\d{1,4})[dD]?", raw):
        chosen = date.today() - timedelta(days=int(match.group(1)))
    else:
        try:
            chosen = date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"since must be YYYY-MM-DD or Nd (days back), got {raw!r}") from None
    return f"{chosen.day:02d}-{_IMAP_MONTHS[chosen.month - 1]}-{chosen.year:04d}"


def _decode_pair(msg: email_lib.message.Message, header: str) -> tuple[str, str]:
    """``(bare address, display text)`` of one header, reusing the adapter's hardened
    parsers (RFC 2047, folding, comments — all attacker-controlled input)."""
    from plugins.platforms.email.adapter import _decode_header_value, _extract_email_address
    raw = _decode_header_value(msg.get(header, "") or "")
    if not raw:
        return "", ""
    bare = _extract_email_address(raw)
    display = raw.split("<")[0].strip().strip('"') if not bare or "<" in raw else bare
    return bare, display


def _summary_of(imap, uid: str) -> dict[str, Any]:
    """One searched message: flags plus the requested header fields (body never fetched)."""
    from plugins.platforms.email.adapter import _imap_payload
    status, data = imap.uid("fetch", uid, _FETCH_HEADERS)
    if status != "OK" or (payload := _imap_payload(data)) is None:
        raise ValueError(f"Fetching header details for UID {uid} failed mid-batch; narrow the search to recover.")
    msg = BytesParser().parsebytes(payload)
    sender, sender_name = _decode_pair(msg, "From")
    to_addr, _ = _decode_pair(msg, "To")
    cc_addr, _ = _decode_pair(msg, "Cc")
    meta_text = data[0][0] if data else b""
    # The IMAP FLAGS literal is ASCII (RFC 3501); decode-with-replace keeps hostile bytes benign.
    flags = (bytes(meta_text) if isinstance(meta_text, (bytes, bytearray)) else str(meta_text).encode()).decode("latin-1", "replace")
    return {
        "uid": uid, "seen": "\\Seen" in flags, "from": sender, "from_name": sender_name,
        "to": to_addr, "cc": cc_addr,
        "subject": str(msg.get("Subject", "") or "").strip(),
        "date": str(msg.get("Date", "") or "").strip(),
        "message_id": str(msg.get("Message-ID", "") or "").strip(),
    }


def search_mailbox(folder: str = "INBOX", *, unread_only: bool = False, sender: str = "",
                   subject: str = "", since: str = "", limit: int = SEARCH_DEFAULT) -> dict[str, Any]:
    """Search one folder server-side and return newest-first header summaries."""
    adapter = _require_adapter()
    keys: list[str] = ["ALL"]
    if unread_only:
        keys.append("UNSEEN")
    for name, value in (("FROM", sender), ("SUBJECT", subject)):
        if str(value or "").strip():
            keys += [name, _search_string(value)]
    if since_token := _search_since(since):
        keys += ["SINCE", since_token]
    capped = max(1, min(int(limit or SEARCH_DEFAULT), SEARCH_MAX))
    with _open_mailbox(adapter, folder) as imap:
        status, data = imap.uid("search", None, *keys)
        uids = [u.decode() if isinstance(u, bytes) else str(u)
                for u in (data[0].split() if status == "OK" and data and data[0] else [])]
        # UID order is arrival order: the tail is the newest slice, presented newest-first.
        return {
            "folder": folder, "mailbox_address": adapter._address,
            "matched": len(uids), "returned": min(len(uids), capped),
            "result_capped": len(uids) > capped,
            "messages": [_summary_of(imap, uid) for uid in reversed(uids[-capped:])],
        }


def _read_body(msg: email_lib.message.Message, body_chars: int) -> tuple[str, bool]:
    """Body text capped at *body_chars* plus whether the cap fired."""
    from plugins.platforms.email.adapter import _extract_text_body
    body = _extract_text_body(msg)
    if len(body) <= body_chars:
        return body, False
    return (body[:body_chars] + f"\n...(truncated at {body_chars} characters — fetch again with "
            f"body_chars up to {READ_BODY_CHARS_MAX} for the rest)"), True


def read_message(uid: str, folder: str = "INBOX", mark_read: bool = True,
                 body_chars: int = READ_BODY_CHARS_DEFAULT) -> dict[str, Any]:
    """Full message for a UID from email_search: headers, body text, cached attachment files.

    Fetches with BODY.PEEK so the \\Seen flag only moves when *mark_read* says so (plain
    RFC822 fetches mark everything read as a side effect)."""
    from plugins.platforms.email.adapter import _extract_attachments, _imap_payload
    adapter = _require_adapter()
    clean_uid = str(uid or "").strip()
    if not clean_uid.isdigit():
        raise ValueError("uid must be the plain numeric UID email_search returned.")
    body_chars = max(200, min(int(body_chars or READ_BODY_CHARS_DEFAULT), READ_BODY_CHARS_MAX))
    with _open_mailbox(adapter, folder) as imap:
        status, data = imap.uid("fetch", clean_uid, "(FLAGS BODY.PEEK[])")
        if status != "OK" or (payload := _imap_payload(data)) is None:
            raise ValueError(f"UID {clean_uid} not found in {folder!r}: run email_search again — "
                             "UIDs are server-assigned and not stable across folder reorders.")
        meta_text = data[0][0] if data else b""
        flags = (bytes(meta_text) if isinstance(meta_text, (bytes, bytearray)) else str(meta_text).encode()).decode("latin-1", "replace")
        was_seen = "\\Seen" in flags
        msg = email_lib.message_from_bytes(payload)
        sender, sender_name = _decode_pair(msg, "From")
        to_addr, _ = _decode_pair(msg, "To")
        cc_addr, _ = _decode_pair(msg, "Cc")
        body, body_truncated = _read_body(msg, body_chars)
        attachments = [
            {**{key: att[key] for key in ("filename", "type", "media_type")}, "path": str(att["path"])}
            for att in _extract_attachments(msg, skip_attachments=False)
        ]
        if mark_read and not was_seen:
            imap.uid("store", clean_uid, "+FLAGS.SILENT", r"(\Seen)")
        return {
            "uid": clean_uid, "folder": folder, "seen": True if mark_read else was_seen,
            "from": sender, "from_name": sender_name, "to": to_addr, "cc": cc_addr,
            "subject": str(msg.get("Subject", "") or "").strip(),
            "date": str(msg.get("Date", "") or "").strip(),
            "message_id": str(msg.get("Message-ID", "") or "").strip(),
            "body": body, "body_truncated": body_truncated, "attachments": attachments,
        }


def list_folders() -> dict[str, Any]:
    """Every selectable mailbox name (flags kept visible, UTF-7 spelling untouched) via LIST."""
    from plugins.platforms.email.adapter import _close_imap, _send_imap_id
    adapter = _require_adapter()
    imap = adapter._connect_imap()
    try:
        imap.login(adapter._address, adapter._password)
        _send_imap_id(imap)
        status, lines = imap.list()
        if status != "OK":
            raise ValueError(f"Listing IMAP folders failed ({_imap_family(status, lines)}).")
        folders: list[dict[str, str]] = []
        for line in lines or []:
            if isinstance(line, (bytes, bytearray)) and (match := _FOLDER_LINE_RE.match(line.decode("utf-8", "replace"))):
                mailbox = match.group("mbox")
                if mailbox.startswith('"') and mailbox.endswith('"') and len(mailbox) >= 2:
                    mailbox = mailbox[1:-1]
                folders.append({"name": mailbox, "delimiter": match.group("sep"),
                                "flags": match.group("flags").strip()})
        return {
            "mailbox_address": adapter._address, "folders": folders,
            "note": "Pass a returned name verbatim as the folder argument. UTF-7-encoded names "
                    "(\\&ABx-) are the literal names IMAP expects; INBOX always exists.",
        }
    finally:
        _close_imap(imap)


def _recipients(raw: str, field: str, required: bool) -> list[str]:
    """Bare addr-spec list for one recipient header. Invalid tokens are dropped; a
    required-empty outcome fails with the field's name so the model can retry precisely."""
    addrs = [addr for _, addr in getaddresses([str(raw or "")])
             if addr and "@" in addr and "/" not in addr and not addr.startswith('"')]
    if required and not addrs:
        raise ValueError(f"{field} requires at least one valid email address (got {raw!r}).")
    return addrs


def _build_message(sender: str, to: list[str], cc: list[str], bcc: list[str], subject: str,
                   body: str, attachments: list[str], reply_to_message_id: str) -> tuple[MIMEMultipart, list[str]]:
    """RFC822 message and the attachment paths that could not be attached.

    Bcc rides ONLY in the SMTP envelope (added at send time), never in a header."""
    from plugins.platforms.email.adapter import _attach_file
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if subject:
        # Non-ASCII subjects go out as a UTF-8 encoded-word; ASCII ones stay plain.
        msg["Subject"] = subject if subject.isascii() else Header(subject, "utf-8")
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<hermes-tool-{uuid.uuid4().hex[:12]}@{(sender.rsplit('@', 1)[-1] or 'localhost')}>"
    if thread_id := str(reply_to_message_id or "").strip():
        msg["In-Reply-To"] = thread_id
        msg["References"] = thread_id
    msg.attach(MIMEText(body, "plain", "utf-8"))
    attached: list[str] = []
    skipped: list[str] = []
    for raw_path in attachments:
        path = Path(str(raw_path).strip())
        if not path.is_file():
            skipped.append(str(path))
            continue
        _attach_file(msg, path, path.name)
        attached.append(path.name)
    return msg, attached, skipped


def _append_sent_copy(adapter, sent_folder: str, msg: MIMEMultipart) -> str:
    """Best-effort server-side Sent copy via IMAP APPEND; a warning or ''.

    APPEND needs no SELECT, so an absent folder is a plain server refusal reported to
    the caller instead of guessing a server-specific fallback name."""
    from plugins.platforms.email.adapter import _close_imap, _send_imap_id
    imap = adapter._connect_imap()
    try:
        imap.login(adapter._address, adapter._password)
        _send_imap_id(imap)
        mailbox = f'"{sent_folder}"' if " " in sent_folder else sent_folder
        status, detail = imap.append(mailbox, None, None, msg.as_bytes())
        if status != "OK":
            return (f"Email sent, but saving to '{sent_folder}' failed ({_imap_family(status, detail)}). "
                    "Retry with save_to_sent=false or a name from email_folders.")
        return ""
    except Exception as exc:
        return f"Email sent, but saving to '{sent_folder}' failed ({exc})."
    finally:
        _close_imap(imap)


def send_mail(*, to: str, body: str, subject: str = "", cc: str = "", bcc: str = "",
              attachments: list[str] | None = None, reply_to_message_id: str = "",
              save_to_sent: bool = False, sent_folder: str = "Sent") -> dict[str, Any]:
    """Compose and deliver one email via the adapter's SMTP path; Bcc stays in the envelope.

    SMTP errors surface the adapter's own diagnostics (e.g. Gmail's app-password advice)."""
    adapter = _require_adapter()
    to_addrs = _recipients(to, "to", required=True)
    cc_addrs = _recipients(cc, "cc", required=False)
    bcc_addrs = _recipients(bcc, "bcc", required=False)
    if not str(body or "").strip():
        raise ValueError("body is required (plain text).")
    msg, attached, skipped = _build_message(
        adapter._address, to_addrs, cc_addrs, bcc_addrs, str(subject or "").strip(), str(body),
        list(attachments or []), str(reply_to_message_id or ""))
    envelope = to_addrs + cc_addrs + bcc_addrs
    smtp = adapter._connect_smtp()
    try:
        smtp.login(adapter._address, adapter._password)
        # An explicit envelope delivers Bcc; send_message only builds one from headers.
        smtp.send_message(msg, from_addr=adapter._address, to_addrs=envelope)
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()
    logger.info("[Email] Tool-sent mail to %s (subject: %r)", envelope, msg["Subject"])
    result: dict[str, Any] = {
        "success": True, "message_id": str(msg["Message-ID"]), "from": adapter._address,
        "to": to_addrs, "cc": cc_addrs, "bcc_count": len(bcc_addrs),
        "subject": str(msg["Subject"] or ""),
        "attachments_attached": attached, "attachments_skipped": skipped,
    }
    if save_to_sent and (warning := _append_sent_copy(adapter, str(sent_folder or "Sent"), msg)):
        result["saved_to_sent"] = False
        result["warnings"] = [warning]
    elif save_to_sent:
        result["saved_to_sent"] = True
    return result
