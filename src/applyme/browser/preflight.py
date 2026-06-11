"""Egress-IP reputation pre-flight — the deciding variable for the unattended silent-pass.

hCaptcha Enterprise weights IP/ASN reputation heavily in its passive risk score (mobile/CGNAT ≫ clean
residential ≫ datacenter). A dirty exit IP is the single most common reason a perfectly-stealthed session
is still blocked, so we check the egress IP BEFORE launching the browser and abort without burning a Lever
attempt when it fails. Pure HTTP (no browser, no Lever cost). The check is best-effort: with no IPQS key it
returns ``unknown`` and the caller proceeds (the gate informs, it doesn't hard-block by default).

Decision rule (IPQualityScore): pass when fraud_score is low, no proxy/VPN/Tor/recent-abuse flags, and the
connection is a real residential/mobile/corporate line — never a datacenter ASN.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger()

_GOOD_CONNECTION_TYPES = {"Residential", "Mobile", "Corporate"}
_FRAUD_SCORE_CEILING = 25  # IPQS 0-100; ≤25 is the "clean enough for a passive Enterprise key" band


@dataclass(frozen=True)
class IpVerdict:
    """Outcome of the egress-IP reputation pre-flight."""

    ok: bool
    ip: str | None
    fraud_score: int | None
    reason: str


def evaluate_ip(info: dict[str, object]) -> tuple[bool, str]:
    """Pure decision over an IPQualityScore /ip response — pass/fail + a human reason.

    Separated from the HTTP call so the gate logic is unit-tested without a network round-trip. An
    *unscoreable* response (IPQS ``success:false`` — bad key/plan/params — or no ``fraud_score``) is
    treated as **advisory** (``ok=True``, proceed) and surfaces IPQS's own message, rather than being
    mistaken for a genuinely dirty IP. Only a real high score / proxy flag / datacenter ASN fails the gate.
    """
    if info.get("success") is False or "fraud_score" not in info:
        msg = info.get("message") or "no fraud_score in response"
        return True, f"unscored — IPQS: {msg} (check the key/plan; proceeding without the IP gate)"
    raw = info["fraud_score"]  # NB: 0 is a valid (clean) score — don't `or`-default it
    score = int(raw) if isinstance(raw, int | float) else 100
    flags = [k for k in ("proxy", "vpn", "tor", "recent_abuse", "bot_status") if info.get(k)]
    conn = str(info.get("connection_type", "") or "")
    if score > _FRAUD_SCORE_CEILING:
        return False, f"fraud_score={score} > {_FRAUD_SCORE_CEILING}"
    if flags:
        return False, f"flagged: {', '.join(flags)}"
    if conn and conn not in _GOOD_CONNECTION_TYPES:
        return False, f"connection_type={conn!r} (want residential/mobile/corporate)"
    return True, f"clean (fraud_score={score}, connection_type={conn or 'unknown'})"


async def check_egress_ip(ipqs_api_key: str | None) -> IpVerdict:
    """Look up the current egress IP's reputation; best-effort, never raises.

    Returns ``ok=True`` with reason ``unknown`` when no IPQS key is set (the caller proceeds — the gate is
    advisory unless the operator wants to hard-block on a dirty IP).
    """
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as h:
        try:
            ip = (await h.get("https://api.ipify.org")).text.strip()
        except httpx.HTTPError as e:
            return IpVerdict(ok=True, ip=None, fraud_score=None, reason=f"egress-IP lookup failed: {e}")
        if not ipqs_api_key:
            return IpVerdict(ok=True, ip=ip, fraud_score=None, reason="unknown (no IPQS key — pre-flight skipped)")
        try:
            # Canonical host (www) — IPQS redirects bare → www, and httpx does not follow redirects by default.
            url = f"https://www.ipqualityscore.com/api/json/ip/{ipqs_api_key}/{ip}"
            info = (await h.get(url, params={"strictness": 1, "allow_public_access_points": "true"})).json()
        except (httpx.HTTPError, ValueError) as e:
            return IpVerdict(ok=True, ip=ip, fraud_score=None, reason=f"IPQS lookup failed: {e}")
    ok, reason = evaluate_ip(info)
    score = info.get("fraud_score")
    verdict = IpVerdict(ok=ok, ip=ip, fraud_score=int(score) if isinstance(score, int) else None, reason=reason)
    log.info("ip_preflight", ip=ip, ok=ok, reason=reason)
    return verdict
