"""email_* model tools: the profile's configured mailbox as agent surface.

The gateway's email channel only routes authorized inbound mail into conversation; these
tools let the agent work the SAME mailbox itself — search headers, read full messages,
list folders, compose and send. Tools are registered in the ``email`` / ``email_send``
toolsets and service-gated by a shared check_fn: they surface only for profiles where an
email mailbox actually is configured (EMAIL_* secrets or ``platforms.email`` in
config.yaml), and ``email_send`` sits in its own one-tool toolset so subagent roles can
deny outgoing sends without losing mailbox reads (tools/delegate_tool_toolsets.py).
"""

from __future__ import annotations

import asyncio
import json
import logging

from tools import email_tool_runtime as runtime
from tools.registry import registry

logger = logging.getLogger(__name__)


def _mailbox_configured() -> bool:
    """One probe answers for the whole family: a configured mailbox is the only gate."""
    return runtime.email_configured()


def _search_args(args: dict) -> tuple:
    return str(args.get("folder") or "INBOX"), dict(
        unread_only=bool(args.get("unread_only", False)),
        sender=str(args.get("sender") or ""),
        subject=str(args.get("subject") or ""),
        since=str(args.get("since") or ""),
        limit=int(args.get("limit") or runtime.SEARCH_DEFAULT),
    )


async def _handle_email_search(args: dict, **kw) -> str:
    folder, filters = _search_args(args)
    return json.dumps(await asyncio.to_thread(runtime.search_mailbox, folder, **filters))


async def _handle_email_read(args: dict, **kw) -> str:
    payload = await asyncio.to_thread(
        runtime.read_message,
        uid=str(args.get("uid") or ""),
        folder=str(args.get("folder") or "INBOX"),
        mark_read=bool(args.get("mark_read", True)),
        body_chars=int(args.get("body_chars") or runtime.READ_BODY_CHARS_DEFAULT),
    )
    return json.dumps(payload)


async def _handle_email_folders(args: dict, **kw) -> str:
    return json.dumps(await asyncio.to_thread(runtime.list_folders))


async def _handle_email_send(args: dict, **kw) -> str:
    payload = await asyncio.to_thread(
        runtime.send_mail,
        to=str(args.get("to") or ""),
        body=str(args.get("body") or ""),
        subject=str(args.get("subject") or ""),
        cc=str(args.get("cc") or ""),
        bcc=str(args.get("bcc") or ""),
        attachments=[str(p) for p in (args.get("attachments") or [])],
        reply_to_message_id=str(args.get("reply_to_message_id") or ""),
        save_to_sent=bool(args.get("save_to_sent", False)),
        sent_folder=str(args.get("sent_folder") or "Sent"),
    )
    return json.dumps(payload)


_EMAIL_SEARCH_SCHEMA = {
    "name": "email_search",
    "description": (
        "Search the configured mailbox: server-side IMAP filter, headers newest-first. "
        "For this profile's own email account (the EMAIL_* secrets). Returns UID, sender "
        "name/address, subject, date, read state; no message bodies are downloaded. Follow "
        "with email_read using a returned UID."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {"type": "string",
                       "description": "Folder to search (default INBOX). Run email_folders for valid names."},
            "unread_only": {"type": "boolean",
                            "description": "Only messages not yet read (UNSEEN). Default false."},
            "sender": {"type": "string",
                       "description": "Match the From header against this substring."},
            "subject": {"type": "string",
                        "description": "Match the Subject header against this substring."},
            "since": {"type": "string",
                      "description": "Received since: YYYY-MM-DD, or Nd meaning N days back (e.g. 7d)."},
            "limit": {"type": "integer",
                      "description": "Max messages to return (newest first). Default "
                                     f"{runtime.SEARCH_DEFAULT}, max {runtime.SEARCH_MAX}."},
        },
        "required": [],
    },
}

_EMAIL_READ_SCHEMA = {
    "name": "email_read",
    "description": (
        "Read one email message completely after an email_search call. Returns headers, "
        "plain-text body, and attachments as cached local file copies you can read directly. "
        "Marks the message read unless mark_read is false."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "uid": {"type": "string",
                    "description": "Numeric UID from email_search (plain digits)."},
            "folder": {"type": "string",
                       "description": "Folder the UID belongs to (must match the email_search folder). Default INBOX."},
            "mark_read": {"type": "boolean",
                          "description": "Flag the message read server-side. Default true."},
            "body_chars": {"type": "integer",
                           "description": f"Body character cap. Default {runtime.READ_BODY_CHARS_DEFAULT}, max "
                                          f"{runtime.READ_BODY_CHARS_MAX}; re-read with a higher cap for the rest."},
        },
        "required": ["uid"],
    },
}

_EMAIL_FOLDERS_SCHEMA = {
    "name": "email_folders",
    "description": (
        "List the folder names of the configured email account, server-spelled. Pass a "
        "returned name verbatim as the folder argument for email_search or email_read; "
        "UTF-7-encoded names are IMAP's literal spelling."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_EMAIL_SEND_SCHEMA = {
    "name": "email_send",
    "description": (
        "Compose and send one outgoing email from the configured mailbox. From is the "
        "profile's own EMAIL_ADDRESS with no draft step, so use deliberately. Plain-text "
        "body plus optional CC/BCC and local file attachments; reply_to_message_id threads "
        "a reply to a message you read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string",
                   "description": "Recipient address(es), comma-separated for several."},
            "subject": {"type": "string",
                        "description": "Subject line; empty sends a subject-less mail."},
            "body": {"type": "string",
                     "description": "Plain-text body (no markdown)."},
            "cc": {"type": "string",
                   "description": "CC address(es), comma-separated."},
            "bcc": {"type": "string",
                    "description": "BCC address(es), comma-separated; added in the SMTP envelope without a visible header."},
            "attachments": {"type": "array", "items": {"type": "string"},
                            "description": "Local file paths to attach; missing paths are skipped and reported."},
            "reply_to_message_id": {"type": "string",
                                    "description": "Message-ID of the mail being replied to; sets In-Reply-To/References threading."},
            "save_to_sent": {"type": "boolean",
                             "description": "Copy the message into the sent folder server-side (folder names differ; failures are reported)."},
            "sent_folder": {"type": "string",
                            "description": "Folder used when save_to_sent is true (default Sent)."},
        },
        "required": ["to", "body"],
    },
}

_TOOLSET = "email"
registry.register(name="email_search", toolset=_TOOLSET, schema=_EMAIL_SEARCH_SCHEMA,
                  handler=_handle_email_search, check_fn=_mailbox_configured, is_async=True,
                  max_result_size_chars=120_000)
registry.register(name="email_read", toolset=_TOOLSET, schema=_EMAIL_READ_SCHEMA,
                  handler=_handle_email_read, check_fn=_mailbox_configured, is_async=True,
                  max_result_size_chars=120_000)
registry.register(name="email_folders", toolset=_TOOLSET, schema=_EMAIL_FOLDERS_SCHEMA,
                  handler=_handle_email_folders, check_fn=_mailbox_configured, is_async=True)
registry.register(name="email_send", toolset="email_send", schema=_EMAIL_SEND_SCHEMA,
                  handler=_handle_email_send, check_fn=_mailbox_configured, is_async=True)
