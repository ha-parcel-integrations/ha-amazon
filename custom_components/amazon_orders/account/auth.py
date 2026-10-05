"""Amazon device sign-in, adapted from alexapy (Apache-2.0), https://gitlab.com/keatontaylor/alexapy."""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import aiohttp

from ..const import SIGN_IN_LANGUAGE
from .errors import AmazonApiError, AmazonAuthError

APP_NAME = "Alexa Media Player"
# Shown in the user's Amazon device list; distinct so it is not mistaken for Alexa Media Player.
DEVICE_NAME = "Home Assistant Parcels"
CALL_VERSION = "2.2.556530.0"
FALLBACK_API_HOST = "api.amazon.com"

_DEVICE_ID_SUFFIX = "23413249564c5635564d32573831"
_TOKEN_FIELDS = {
    "app_name": APP_NAME,
    "app_version": CALL_VERSION,
    "di.sdk.version": "6.12.4",
    "package_name": "com.amazon.echo",
    "di.hw.version": "iPhone",
    "platform": "iOS",
    "di.os.name": "iOS",
    "di.os.version": "16.6",
    "current_version": "6.12.4",
    "previous_version": "6.12.4",
}


@dataclass(frozen=True)
class DeviceRegistration:
    """What a successful device registration hands back."""

    refresh_token: str
    access_token: str
    expires_in: int


def new_device_serial() -> str:
    """Return a fresh per-entry device serial."""
    return uuid4().hex.upper()


def new_code_verifier() -> str:
    """Return a fresh PKCE code verifier."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _device_id(serial: str) -> str:
    return serial.encode().hex() + _DEVICE_ID_SUFFIX


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def build_sign_in_url(domain: str, serial: str, verifier: str) -> str:
    """Return the Amazon sign-in URL the user opens in their own browser."""
    query = {
        "openid.return_to": "https://www.amazon.com/ap/maplanding",
        "openid.assoc_handle": "amzn_dp_project_dee_ios",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "pageId": "amzn_dp_project_dee_ios",
        "accountStatusPolicy": "P1",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.mode": "checkid_setup",
        "openid.ns.oa2": "http://www.amazon.com/ap/ext/oauth/2",
        "openid.oa2.client_id": f"device:{_device_id(serial)}",
        "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
        "openid.oa2.response_type": "code",
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.pape.max_auth_age": "0",
        "openid.oa2.scope": "device_auth_access offline_access",
        "openid.oa2.code_challenge_method": "S256",
        "openid.oa2.code_challenge": _code_challenge(verifier),
        "language": SIGN_IN_LANGUAGE.get(domain, "en_US"),
    }
    return "https://www.amazon.com/ap/signin?" + urlencode(query)


def extract_authorization_code(landing_url: str) -> str | None:
    """Return the authorization code from the pasted landing URL, if any."""
    try:
        query = parse_qs(urlparse(landing_url.strip()).query)
    except ValueError:
        return None
    values = query.get("openid.oa2.authorization_code")
    return values[0] if values else None


def _api_hosts(domain: str, preferred: str | None) -> list[str]:
    hosts = [f"api.{domain}", FALLBACK_API_HOST]
    if preferred in hosts:
        hosts.remove(preferred)
        hosts.insert(0, preferred)
    return list(dict.fromkeys(hosts))


def _is_rejection(status: int) -> bool:
    """Whether a status means "this sign-in is no good" rather than "slow down"."""
    return 400 <= status < 500 and status not in (408, 429)


async def _post_api(
    session: aiohttp.ClientSession,
    domain: str,
    path: str,
    *,
    preferred_host: str | None = None,
    **request: Any,
) -> tuple[str, dict[str, Any]]:
    """POST to the regional API host, falling back to the global one.

    Returns the host that answered with the parsed JSON body. A 4xx is only
    reported as an auth failure when every host that answered said so;
    otherwise a single host's outage would look like a rejected sign-in.
    """
    statuses: list[int] = []
    last_error: aiohttp.ClientError | None = None
    for host in _api_hosts(domain, preferred_host):
        try:
            async with session.post(f"https://{host}{path}", **request) as response:
                if response.status == 200:
                    return host, await _json_object(response)
                statuses.append(response.status)
        except aiohttp.ClientConnectorError as err:
            last_error = err
    if not statuses:
        assert last_error is not None
        raise last_error
    if all(_is_rejection(status) for status in statuses):
        raise AmazonAuthError(f"HTTP {statuses[-1]}", status_code=statuses[-1])
    raise AmazonApiError(f"HTTP {statuses[-1]}", status_code=statuses[-1])


async def _json_object(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        payload = await response.json(content_type=None)
    except ValueError as err:
        raise AmazonApiError(f"unparseable body ({err})") from err
    if not isinstance(payload, dict):
        raise AmazonApiError("unexpected body (not a JSON object)")
    return payload


async def register_device(
    session: aiohttp.ClientSession,
    domain: str,
    serial: str,
    verifier: str,
    authorization_code: str,
) -> tuple[str, DeviceRegistration]:
    """Exchange the authorization code for a refresh token.

    Returns the API host that accepted the registration along with the tokens.
    """
    frc = base64.b64encode(secrets.token_bytes(313)).decode().rstrip("=")
    payload = {
        "requested_extensions": ["device_info", "customer_info"],
        "cookies": {"website_cookies": [], "domain": f".{domain}"},
        "registration_data": {
            "domain": "Device",
            "app_version": CALL_VERSION,
            "device_type": "A2IVLV5VM2W81",
            "device_name": f"%FIRST_NAME%'s%DUPE_STRATEGY_1ST%{DEVICE_NAME}",
            "os_version": "16.6",
            "device_serial": serial,
            "device_model": "iPhone",
            "app_name": APP_NAME,
            "software_version": "1",
        },
        "auth_data": {
            "client_id": _device_id(serial),
            "authorization_code": authorization_code,
            "code_verifier": verifier,
            "code_algorithm": "SHA-256",
            "client_domain": "DeviceLegacy",
        },
        "user_context_map": {"frc": frc},
        "requested_token_type": ["bearer", "mac_dms", "website_cookies"],
    }
    host, body = await _post_api(session, domain, "/auth/register", json=payload)
    try:
        bearer = body["response"]["success"]["tokens"]["bearer"]
        return host, DeviceRegistration(
            refresh_token=bearer["refresh_token"],
            access_token=bearer["access_token"],
            expires_in=int(bearer["expires_in"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise AmazonAuthError("registration returned no bearer token") from err


async def refresh_access_token(
    session: aiohttp.ClientSession,
    domain: str,
    refresh_token: str,
    *,
    preferred_host: str | None = None,
) -> tuple[str, str]:
    """Renew the access token; returns the answering host and the token."""
    data = {
        **_TOKEN_FIELDS,
        "source_token": refresh_token,
        "requested_token_type": "access_token",
        "source_token_type": "refresh_token",
    }
    host, body = await _post_api(
        session, domain, "/auth/token", preferred_host=preferred_host, data=data
    )
    token = body.get("access_token")
    if not token:
        raise AmazonAuthError("refresh returned no access token")
    return host, str(token)


async def exchange_token_for_cookies(
    session: aiohttp.ClientSession, domain: str, refresh_token: str
) -> list[dict[str, Any]]:
    """Mint website cookies for the storefront from the refresh token.

    Each returned dict carries ``domain`` plus the cookie's ``Name``,
    ``Value``, ``Path``, ``Secure`` and ``HttpOnly`` fields.
    """
    data = {
        **_TOKEN_FIELDS,
        "domain": f".{domain}",
        "source_token": refresh_token,
        "requested_token_type": "auth_cookies",
        "source_token_type": "refresh_token",
    }
    async with session.post(
        f"https://www.{domain}/ap/exchangetoken/cookies", data=data
    ) as response:
        if _is_rejection(response.status):
            raise AmazonAuthError(f"HTTP {response.status}", status_code=response.status)
        if response.status != 200:
            raise AmazonApiError(f"HTTP {response.status}", status_code=response.status)
        body = await _json_object(response)

    try:
        grouped = body["response"]["tokens"]["cookies"]
        cookies = [
            {**item, "domain": cookie_domain}
            for cookie_domain, items in grouped.items()
            for item in items
        ]
    except (KeyError, TypeError, AttributeError) as err:
        raise AmazonAuthError("cookie exchange returned no cookies") from err
    if not cookies:
        raise AmazonAuthError("cookie exchange returned no cookies")
    return cookies
