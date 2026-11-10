"""OAuth (PKCE) against Bosch's Keycloak realm for both data sources.

Two clients are supported, one per account:

* ``mobile``  - the public ``one-bike-app`` client the eBike Flow app itself uses.
  Works for every account (also outside the EU). Bosch redirects to an app
  scheme (``onebikeapp-ios://``) the browser cannot open, so the user copies
  the redirect URL from the browser's address bar / DevTools and pastes it.
* ``dataact`` - the user's own EU Data Act client (``euda-...``), registered in
  the Bosch Data Act portal with redirect ``http://localhost:8888/callback``.
  The browser fails to load localhost, the URL stays in the address bar and
  is pasted the same way.

Endpoints and client details follow what the open-source projects
partymola/bosch-flow-mcp and Xunil99/ha-bosch-ebike document; the upstream
watcher workflow flags changes there.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

AUTH_BASE = "https://p9.authz.bosch.com/auth/realms/obc/protocol/openid-connect"
AUTH_URL = f"{AUTH_BASE}/auth"
TOKEN_URL = f"{AUTH_BASE}/token"

MOBILE_CLIENT_ID = "one-bike-app"
MOBILE_REDIRECT = "onebikeapp-ios://com.bosch.ebike.onebikeapp/oauth2redirect"
DATAACT_REDIRECT = "http://localhost:8888/callback"
SCOPE = "openid offline_access"

SOURCES = ("mobile", "dataact")


class AuthError(Exception):
    """Login or token refresh failed; the account needs a new login."""


@dataclass
class PendingLogin:
    state: str
    verifier: str
    client_id: str
    redirect_uri: str
    url: str


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def client_for(source: str, client_id: str | None) -> tuple[str, str]:
    if source == "mobile":
        return MOBILE_CLIENT_ID, MOBILE_REDIRECT
    if source == "dataact":
        if not client_id:
            raise AuthError("Data Act login needs your own Client-ID from the Bosch portal")
        return client_id, DATAACT_REDIRECT
    raise AuthError(f"unknown source {source!r}")


def start_login(source: str, client_id: str | None = None) -> PendingLogin:
    cid, redirect = client_for(source, client_id)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": cid,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "login",
    }
    if source == "mobile":
        params["kc_idp_hint"] = "skid"
        params["nonce"] = secrets.token_urlsafe(16)
    return PendingLogin(state, verifier, cid, redirect, f"{AUTH_URL}?{urlencode(params)}")


def extract_code(pasted: str, expected_state: str | None = None) -> str:
    """Accept the full redirect URL (or just the code) the user pasted."""
    pasted = (pasted or "").strip()
    if not pasted:
        raise AuthError("Nothing pasted")
    if "code=" not in pasted:
        # A bare code: Keycloak codes contain dots, never spaces.
        if " " in pasted or "/" in pasted:
            raise AuthError("No 'code=' found in the pasted URL")
        return pasted
    query = urlparse(pasted).query or pasted.split("?", 1)[-1]
    qs = parse_qs(query)
    if "error" in qs:
        raise AuthError(f"Bosch login error: {qs['error'][0]}")
    if expected_state and qs.get("state", [expected_state])[0] != expected_state:
        raise AuthError("The pasted URL belongs to a different login attempt - start again")
    try:
        return qs["code"][0]
    except (KeyError, IndexError) as err:
        raise AuthError("No 'code=' found in the pasted URL") from err


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float

    @classmethod
    def from_response(cls, data: dict, old_refresh: str | None = None) -> "Tokens":
        if "access_token" not in data:
            raise AuthError(data.get("error_description") or data.get("error") or "no token")
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token") or old_refresh or "",
            expires_at=time.time() + float(data.get("expires_in", 300)) - 30,
        )


async def _token_request(session: aiohttp.ClientSession, form: dict) -> dict:
    async with session.post(
        TOKEN_URL, data=form, timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        try:
            data = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            data = {"error": (await resp.text())[:200]}
        if resp.status >= 400:
            raise AuthError(
                data.get("error_description") or data.get("error") or f"HTTP {resp.status}"
            )
        return data


async def exchange_code(
    session: aiohttp.ClientSession, pending: PendingLogin, code: str
) -> Tokens:
    data = await _token_request(
        session,
        {
            "grant_type": "authorization_code",
            "client_id": pending.client_id,
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.verifier,
        },
    )
    return Tokens.from_response(data)


async def refresh(session: aiohttp.ClientSession, client_id: str, refresh_token: str) -> Tokens:
    if not refresh_token:
        raise AuthError("No refresh token - please log in again")
    data = await _token_request(
        session,
        {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token},
    )
    return Tokens.from_response(data, old_refresh=refresh_token)
