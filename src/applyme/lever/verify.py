"""Best-effort: capture Lever's confirmation email as evidence (NOT a gate). Links are host-allowlisted."""

import asyncio
import re
from urllib.parse import urlparse

_LINK_RE = re.compile(r'https://[^\s"\'<>]*lever\.co[^\s"\'<>]*')


def is_safe_lever_link(url: str) -> bool:
    """Return True only for https URLs whose host is lever.co or a *.lever.co subdomain (SSRF guard)."""
    p = urlparse(url)
    if p.scheme != "https" or "@" in p.netloc:
        return False
    host = p.hostname or ""
    return host == "lever.co" or host.endswith(".lever.co")


def extract_lever_link(body: str) -> str | None:
    """Find the first safe lever.co link in an email body (HTML or plain-text)."""
    for m in _LINK_RE.finditer(body):
        if is_safe_lever_link(m.group(0)):
            return m.group(0)
    return None


def _poll_once(host: str, user: str, password: str, sender: str = "hire.lever.co") -> str | None:
    """Single IMAP sweep — runs in a thread (blocking)."""
    from imap_tools import AND, MailBox  # imported here to keep module importable without imap_tools in tests

    with MailBox(host).login(user, password, "INBOX") as mb:
        for msg in mb.fetch(AND(seen=False), reverse=True, bulk=True, mark_seen=False):
            if sender in (msg.from_ or ""):
                link = extract_lever_link(msg.html or msg.text or "")
                if link:
                    return link
    return None


async def poll_confirmation(
    host: str,
    user: str,
    password: str,
    deadline_s: float = 120.0,
    interval_s: float = 5.0,
) -> str | None:
    """Poll INBOX for a Lever confirmation email; returns the safe link or None if deadline expires.

    'No email' is expected for silent tenants — this is evidence-only, not a gate.
    """
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    while loop.time() < end:
        link = await asyncio.to_thread(_poll_once, host, user, password)
        if link:
            return link
        await asyncio.sleep(interval_s)
    return None
