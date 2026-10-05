"""Tests for the device sign-in helpers."""
from urllib.parse import parse_qs, urlparse

import pytest

from custom_components.amazon_orders.account.auth import (
    FALLBACK_API_HOST,
    build_sign_in_url,
    exchange_token_for_cookies,
    extract_authorization_code,
    new_code_verifier,
    new_device_serial,
    refresh_access_token,
    register_device,
)
from custom_components.amazon_orders.account.errors import (
    AmazonApiError,
    AmazonAuthError,
)

from ..fakes import FakeResponse, FakeSession, connector_error

DOMAIN = "amazon.nl"
REGISTERED = {
    "response": {
        "success": {
            "tokens": {
                "bearer": {
                    "refresh_token": "Atnr|refresh",
                    "access_token": "Atna|access",
                    "expires_in": "3600",
                }
            }
        }
    }
}


def test_serial_and_verifier_are_fresh_each_time():
    assert new_device_serial() != new_device_serial()
    assert new_code_verifier() != new_code_verifier()
    assert len(new_device_serial()) == 32


def test_sign_in_url_carries_the_pkce_challenge_and_language():
    url = build_sign_in_url(DOMAIN, "SERIAL", "verifier")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.amazon.com"
    assert parsed.path == "/ap/signin"
    assert query["openid.oa2.code_challenge_method"] == ["S256"]
    assert query["openid.oa2.code_challenge"] != ["verifier"]
    assert query["language"] == ["nl_NL"]
    assert query["openid.oa2.client_id"][0].startswith("device:")
    assert "verifier" not in url


def test_unlisted_storefront_signs_in_in_english():
    url = build_sign_in_url("amazon.se", "SERIAL", "verifier")
    assert parse_qs(urlparse(url).query)["language"] == ["en_US"]


def test_authorization_code_is_pulled_from_the_landing_url():
    url = "https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=ABC123&x=1"
    assert extract_authorization_code(f"  {url}  ") == "ABC123"


@pytest.mark.parametrize(
    "value",
    ["https://www.amazon.com/ap/maplanding?x=1", "not a url", "", "http://[bad"],
)
def test_landing_url_without_a_code_gives_none(value):
    assert extract_authorization_code(value) is None


# ---------------------------------------------------------------------------
# register_device
# ---------------------------------------------------------------------------


async def test_registration_returns_the_tokens_and_the_answering_host():
    session = FakeSession()
    session.add("POST", "/auth/register", FakeResponse(200, REGISTERED))

    host, registration = await register_device(session, DOMAIN, "SERIAL", "verifier", "CODE")

    assert host == f"api.{DOMAIN}"
    assert registration.refresh_token == "Atnr|refresh"
    assert registration.access_token == "Atna|access"
    assert registration.expires_in == 3600
    payload = session.calls[0][2]["json"]
    assert payload["auth_data"]["authorization_code"] == "CODE"
    assert payload["auth_data"]["code_verifier"] == "verifier"
    assert payload["registration_data"]["device_serial"] == "SERIAL"
    assert payload["registration_data"]["device_name"].endswith("Home Assistant Parcels")
    assert payload["cookies"]["domain"] == f".{DOMAIN}"


async def test_registration_falls_back_to_the_global_host():
    session = FakeSession()
    session.add("POST", f"api.{DOMAIN}", FakeResponse(404, {}))
    session.add("POST", FALLBACK_API_HOST, FakeResponse(200, REGISTERED))

    host, _ = await register_device(session, DOMAIN, "S", "v", "c")

    assert host == FALLBACK_API_HOST


async def test_registration_falls_back_when_the_regional_host_is_unreachable():
    session = FakeSession()
    session.add("POST", f"api.{DOMAIN}", connector_error())
    session.add("POST", FALLBACK_API_HOST, FakeResponse(200, REGISTERED))

    host, _ = await register_device(session, DOMAIN, "S", "v", "c")

    assert host == FALLBACK_API_HOST


async def test_registration_without_a_bearer_token_is_an_auth_error():
    session = FakeSession()
    session.add("POST", "/auth/register", FakeResponse(200, {"response": {}}))
    with pytest.raises(AmazonAuthError):
        await register_device(session, DOMAIN, "S", "v", "c")


async def test_registration_rejected_everywhere_is_an_auth_error():
    session = FakeSession()
    session.add("POST", "/auth/register", FakeResponse(400, {}))
    with pytest.raises(AmazonAuthError) as excinfo:
        await register_device(session, DOMAIN, "S", "v", "c")
    assert excinfo.value.status_code == 400


async def test_an_outage_is_not_mistaken_for_a_rejected_sign_in():
    """One host 5xx plus one host 4xx must not start a reauth."""
    session = FakeSession()
    session.add("POST", f"api.{DOMAIN}", FakeResponse(503, {}))
    session.add("POST", FALLBACK_API_HOST, FakeResponse(400, {}))
    with pytest.raises(AmazonApiError) as excinfo:
        await register_device(session, DOMAIN, "S", "v", "c")
    assert not isinstance(excinfo.value, AmazonAuthError)


async def test_rate_limiting_is_not_a_rejected_sign_in():
    session = FakeSession()
    session.add("POST", "/auth/register", FakeResponse(429, {}))
    with pytest.raises(AmazonApiError) as excinfo:
        await register_device(session, DOMAIN, "S", "v", "c")
    assert not isinstance(excinfo.value, AmazonAuthError)
    assert excinfo.value.status_code == 429


async def test_unreachable_everywhere_raises_the_connection_error():
    session = FakeSession()
    session.add("POST", "/auth/register", connector_error())
    with pytest.raises(type(connector_error())):
        await register_device(session, DOMAIN, "S", "v", "c")


async def test_unparseable_body_is_an_api_error():
    session = FakeSession()
    session.add("POST", "/auth/register", FakeResponse(200, ValueError("bad")))
    with pytest.raises(AmazonApiError):
        await register_device(session, DOMAIN, "S", "v", "c")


async def test_non_object_body_is_an_api_error():
    session = FakeSession()
    session.add("POST", "/auth/register", FakeResponse(200, ["nope"]))
    with pytest.raises(AmazonApiError):
        await register_device(session, DOMAIN, "S", "v", "c")


# ---------------------------------------------------------------------------
# refresh_access_token
# ---------------------------------------------------------------------------


async def test_refresh_returns_the_access_token_and_prefers_the_known_host():
    session = FakeSession()
    session.add("POST", "/auth/token", FakeResponse(200, {"access_token": "new"}))

    host, token = await refresh_access_token(
        session, DOMAIN, "refresh", preferred_host=FALLBACK_API_HOST
    )

    assert (host, token) == (FALLBACK_API_HOST, "new")
    assert session.calls[0][1].startswith(f"https://{FALLBACK_API_HOST}/")
    assert session.calls[0][2]["data"]["source_token"] == "refresh"


async def test_refresh_without_an_access_token_is_an_auth_error():
    session = FakeSession()
    session.add("POST", "/auth/token", FakeResponse(200, {}))
    with pytest.raises(AmazonAuthError):
        await refresh_access_token(session, DOMAIN, "refresh")


async def test_refresh_rejected_is_an_auth_error():
    session = FakeSession()
    session.add("POST", "/auth/token", FakeResponse(401, {}))
    with pytest.raises(AmazonAuthError):
        await refresh_access_token(session, DOMAIN, "refresh")


# ---------------------------------------------------------------------------
# exchange_token_for_cookies
# ---------------------------------------------------------------------------

COOKIES = {
    "response": {
        "tokens": {
            "cookies": {
                f".{DOMAIN}": [
                    {"Name": "session-id", "Value": '"abc"', "Path": "/", "Secure": True}
                ]
            }
        }
    }
}


async def test_exchange_flattens_cookies_with_their_domain():
    session = FakeSession()
    session.add("POST", "/ap/exchangetoken/cookies", FakeResponse(200, COOKIES))

    cookies = await exchange_token_for_cookies(session, DOMAIN, "refresh")

    assert cookies == [
        {
            "Name": "session-id",
            "Value": '"abc"',
            "Path": "/",
            "Secure": True,
            "domain": f".{DOMAIN}",
        }
    ]
    assert session.calls[0][2]["data"]["domain"] == f".{DOMAIN}"


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_exchange_rejection_is_an_auth_error(status):
    session = FakeSession()
    session.add("POST", "/ap/exchangetoken/cookies", FakeResponse(status, {}))
    with pytest.raises(AmazonAuthError):
        await exchange_token_for_cookies(session, DOMAIN, "refresh")


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_exchange_outage_is_not_an_auth_error(status):
    session = FakeSession()
    session.add("POST", "/ap/exchangetoken/cookies", FakeResponse(status, {}))
    with pytest.raises(AmazonApiError) as excinfo:
        await exchange_token_for_cookies(session, DOMAIN, "refresh")
    assert not isinstance(excinfo.value, AmazonAuthError)
    assert excinfo.value.status_code == status


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"response": {"tokens": {"cookies": {}}}},
        {"response": {"tokens": {"cookies": "junk"}}},
    ],
)
async def test_exchange_without_cookies_is_an_auth_error(body):
    session = FakeSession()
    session.add("POST", "/ap/exchangetoken/cookies", FakeResponse(200, body))
    with pytest.raises(AmazonAuthError):
        await exchange_token_for_cookies(session, DOMAIN, "refresh")
