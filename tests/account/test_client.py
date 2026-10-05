"""Tests for the Amazon page client: auth, fan-out caps and failure mapping."""
from datetime import date, timedelta

import pytest

from custom_components.amazon_orders.account import client as client_module
from custom_components.amazon_orders.account.client import AmazonClient
from custom_components.amazon_orders.account.errors import (
    AmazonApiError,
    AmazonAuthError,
)

from ..fakes import FakeResponse, FakeSession
from ..payloads import (
    ACTIVE_CODE,
    COUNTRY,
    DELIVERED_EVENTS,
    orders_page,
    pop_page,
    tile,
    track_page,
    track_path,
)

COOKIES = {
    "response": {
        "tokens": {
            "cookies": {
                f".{COUNTRY}": [
                    {"Name": "session-id", "Value": '"abc"', "Path": "/", "Secure": True},
                    {"Name": "at-main", "Value": "def", "Secure": False},
                ]
            }
        }
    }
}


def _day(offset_days: int) -> str:
    moment = date.today() - timedelta(days=offset_days)
    return f"{moment.day} {moment:%B}"


def _placed(offset_days: int) -> str:
    moment = date.today() - timedelta(days=offset_days)
    return f"{moment.day} {moment:%B %Y}"


def _delivered_tile(shipment: str, days_ago: int = 1, **kwargs) -> str:
    return tile(
        shipment,
        f"Delivered {_day(days_ago)}",
        placed=_placed(days_ago + 2),
        **kwargs,
    )


def _active_tile(shipment: str, **kwargs) -> str:
    return tile(shipment, "Arriving today", None, placed=_placed(1), **kwargs)


def _session(*tiles: str, orders_status: int = 200) -> FakeSession:
    session = FakeSession()
    session.add("POST", "/auth/token", FakeResponse(200, {"access_token": "new"}))
    session.add("POST", "/ap/exchangetoken/cookies", FakeResponse(200, COOKIES))
    session.add("GET", "/gp/css/order-history", FakeResponse(200, orders_page()))
    session.add(
        "GET",
        "/your-orders/orders",
        FakeResponse(orders_status, orders_page(*tiles)),
    )
    return session


def _shipment(session: FakeSession, shipment: str, *, tracked: bool = True, **page):
    """Script the pop page (and track page) for one shipment."""
    path = track_path(shipment)
    session.add(
        "GET",
        f"pop?ref=ppx_test&orderId=000-0000001-0000001&lineItemId=lineitem0001&shipmentId={shipment}",
        FakeResponse(200, pop_page(path if tracked else None)),
    )
    if tracked:
        session.add(
            "GET",
            f"orderId=000-0000001-0000001&shipmentId={shipment}",
            FakeResponse(200, track_page(**page)),
        )


def _client(session: FakeSession) -> AmazonClient:
    return AmazonClient(COUNTRY, "refresh", session)


def _gets(session: FakeSession) -> list[str]:
    return [url for method, url, _ in session.calls if method == "GET"]


# ---------------------------------------------------------------------------
# a normal poll
# ---------------------------------------------------------------------------


async def test_poll_authenticates_then_reads_orders_pop_and_track():
    session = _session(_active_tile("SHIPactive1"))
    _shipment(
        session,
        "SHIPactive1",
        events=DELIVERED_EVENTS[1:],
        milestone="IN_PROGRESS",
        tracking_id=ACTIVE_CODE,
    )

    records = await _client(session).async_get_parcels()

    assert [r["tracking_id"] for r in records] == [ACTIVE_CODE]
    assert records[0]["carrier_code"] == "DRAGONFLY"
    assert records[0]["milestone"] == "IN_PROGRESS"
    posts = [url for method, url, _ in session.calls if method == "POST"]
    assert posts[0].endswith("/auth/token")
    assert posts[1].endswith("/ap/exchangetoken/cookies")
    gets = _gets(session)
    assert gets[0].endswith("/your-orders/orders")
    assert "your-orders/pop" in gets[1]
    assert "ship-track" in gets[2]
    # Page loads identify as a mobile browser.
    assert "iPhone" in session.calls[2][2]["headers"]["User-Agent"]


async def test_exchanged_cookies_land_in_the_session_jar():
    session = _session()
    await _client(session).async_get_parcels()

    stored = [call.args for call in session.cookie_jar.update_cookies.call_args_list]
    assert len(stored) == 2
    cookie, url = stored[0]
    assert cookie["session-id"].value == "abc"
    assert cookie["session-id"]["domain"] == f".{COUNTRY}"
    assert cookie["session-id"]["path"] == "/"
    assert str(url) == f"https://www.{COUNTRY}/"
    assert not stored[1][0]["at-main"]["secure"]


async def test_a_delivered_shipment_is_read_once_and_then_cached():
    session = _session(_delivered_tile("SHIPdone001"))
    _shipment(session, "SHIPdone001", events=DELIVERED_EVENTS)
    client = _client(session)

    first = await client.async_get_parcels()
    reads_after_first = len(_gets(session))
    second = await client.async_get_parcels()

    assert second == first
    assert first[0]["milestone"] == "DELIVERED"
    # Only the orders page was loaded again.
    assert len(_gets(session)) == reads_after_first + 1


async def test_in_flight_shipment_is_re_read_every_poll():
    session = _session(_active_tile("SHIPactive1"))
    _shipment(session, "SHIPactive1", milestone="IN_PROGRESS")
    client = _client(session)

    await client.async_get_parcels()
    await client.async_get_parcels()

    assert sum("ship-track" in url for url in _gets(session)) == 2


async def test_delivered_long_ago_is_not_followed():
    session = _session(_delivered_tile("SHIPold0001", days_ago=30))
    assert await _client(session).async_get_parcels() == []
    assert len(_gets(session)) == 1  # the orders page only


async def test_untracked_shipment_stays_order_level():
    session = _session(_active_tile("SHIPnotrack1"))
    _shipment(session, "SHIPnotrack1", tracked=False)

    (record,) = await _client(session).async_get_parcels()

    assert record["barcode_source"] == "shipment_key"
    assert record["tracking_id"] is None
    assert not any("ship-track" in url for url in _gets(session))


async def test_delivered_shipment_without_a_tracking_page_is_final_too():
    session = _session(_delivered_tile("SHIPnotrack1"))
    _shipment(session, "SHIPnotrack1", tracked=False)
    client = _client(session)

    await client.async_get_parcels()
    await client.async_get_parcels()

    assert sum("pop?" in url for url in _gets(session)) == 1


async def test_order_lines_of_one_shipment_cost_one_read():
    session = _session(
        _active_tile("SHIPactive1", line_item="lineitem0001"),
        _active_tile("SHIPactive1", line_item="lineitem0002", title="Second item"),
    )
    _shipment(session, "SHIPactive1", milestone="IN_PROGRESS")

    (record,) = await _client(session).async_get_parcels()

    assert record["items"] == ["Example Widget, Blue", "Second item"]
    assert sum("pop?" in url for url in _gets(session)) == 1


async def test_pause_between_page_loads(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(client_module, "REQUEST_PAUSE_SECONDS", 1.5)
    session = _session(_active_tile("SHIPactive1"))
    _shipment(session, "SHIPactive1", milestone="IN_PROGRESS")

    await _client(session).async_get_parcels()

    assert sleeps == [1.5, 1.5]  # before pop and before track, not before orders


async def test_regional_api_host_is_remembered_between_polls():
    session = _session()
    client = _client(session)
    await client.async_get_parcels()
    await client.async_get_parcels()
    assert client._api_host == f"api.{COUNTRY}"


# ---------------------------------------------------------------------------
# the fan-out cap
# ---------------------------------------------------------------------------


async def test_a_poll_follows_at_most_the_capped_number_of_shipments(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_TRACK_LOADS", 2)
    shipments = [f"SHIP{n:07d}" for n in range(5)]
    session = _session(*(_active_tile(s) for s in shipments))
    for shipment in shipments:
        _shipment(session, shipment, milestone="IN_PROGRESS")
    client = _client(session)

    first = await client.async_get_parcels()
    assert sum("ship-track" in url for url in _gets(session)) == 2
    assert [r["shipment_id"] for r in first] == shipments[:2]

    # The next cycles rotate to the shipments that have not been read yet,
    # then back to the stalest one; nothing is read twice in a row.
    second = await client.async_get_parcels()
    third = await client.async_get_parcels()
    assert [r["shipment_id"] for r in second] == shipments[:4]
    assert [r["shipment_id"] for r in third] == shipments


async def test_shipments_that_left_the_orders_page_are_forgotten():
    session = _session(_delivered_tile("SHIPdone001"))
    _shipment(session, "SHIPdone001", events=DELIVERED_EVENTS)
    client = _client(session)
    await client.async_get_parcels()
    assert client._final and client._last

    session.add("GET", "/your-orders/orders", FakeResponse(200, orders_page()))
    session.add("GET", "/gp/css/order-history", FakeResponse(200, orders_page()))

    assert await client.async_get_parcels() == []
    assert not client._final and not client._last


# ---------------------------------------------------------------------------
# the orders page and its fallback
# ---------------------------------------------------------------------------


async def test_legacy_history_page_is_the_fallback_for_a_missing_orders_page():
    session = _session(orders_status=404)
    session.add(
        "GET",
        "/gp/css/order-history",
        FakeResponse(200, orders_page(_active_tile("SHIPactive1"))),
    )
    _shipment(session, "SHIPactive1", milestone="IN_PROGRESS")

    records = await _client(session).async_get_parcels()

    assert len(records) == 1


async def test_legacy_history_page_is_tried_when_the_orders_page_has_no_lines():
    session = _session()
    session.add(
        "GET",
        "/gp/css/order-history",
        FakeResponse(200, orders_page(_active_tile("SHIPactive1"))),
    )
    _shipment(session, "SHIPactive1", milestone="IN_PROGRESS")

    assert len(await _client(session).async_get_parcels()) == 1


async def test_an_account_without_orders_gives_no_records():
    session = _session()
    session.add("GET", "/gp/css/order-history", FakeResponse(200, orders_page()))
    assert await _client(session).async_get_parcels() == []


async def test_both_order_pages_failing_raises_the_failure():
    session = _session(orders_status=500)
    session.add("GET", "/gp/css/order-history", FakeResponse(500, ""))
    with pytest.raises(AmazonApiError) as excinfo:
        await _client(session).async_get_parcels()
    assert excinfo.value.status_code == 500


async def test_rate_limiting_on_the_orders_page_does_not_fall_back():
    session = _session(orders_status=429)
    with pytest.raises(AmazonApiError) as excinfo:
        await _client(session).async_get_parcels()
    assert excinfo.value.status_code == 429
    assert not any("order-history" in url for url in _gets(session))


# ---------------------------------------------------------------------------
# failure mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_pages_are_an_auth_error(status):
    session = _session(orders_status=status)
    with pytest.raises(AmazonAuthError):
        await _client(session).async_get_parcels()


async def test_a_redirect_to_the_sign_in_page_is_an_auth_error():
    session = _session()
    session.add(
        "GET",
        "/your-orders/orders",
        FakeResponse(200, "<html></html>", url="https://www.amazon.nl/ap/signin?x=1"),
    )
    with pytest.raises(AmazonAuthError):
        await _client(session).async_get_parcels()


async def test_a_password_form_is_an_auth_error():
    session = _session()
    session.add(
        "GET",
        "/your-orders/orders",
        FakeResponse(200, '<input type="password" name="password">'),
    )
    with pytest.raises(AmazonAuthError):
        await _client(session).async_get_parcels()


async def test_a_rejected_refresh_token_is_an_auth_error():
    session = FakeSession()
    session.add("POST", "/auth/token", FakeResponse(400, {}))
    with pytest.raises(AmazonAuthError):
        await _client(session).async_get_parcels()


@pytest.mark.parametrize(
    "headers,expected", [({"Retry-After": "90"}, 90.0), ({"Retry-After": "Wed, 1 Jan"}, None), ({}, None)]
)
async def test_rate_limited_page_reports_retry_after(headers, expected):
    session = _session()
    session.add("GET", "/your-orders/orders", FakeResponse(429, "", headers=headers))
    with pytest.raises(AmazonApiError) as excinfo:
        await _client(session).async_get_parcels()
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == expected


async def test_service_unavailable_is_reported_like_rate_limiting():
    session = _session(orders_status=503)
    with pytest.raises(AmazonApiError) as excinfo:
        await _client(session).async_get_parcels()
    assert excinfo.value.status_code == 503


async def test_a_failing_pop_page_fails_the_poll():
    session = _session(_active_tile("SHIPactive1"))
    session.add("GET", "shipmentId=SHIPactive1", FakeResponse(500, ""))
    with pytest.raises(AmazonApiError):
        await _client(session).async_get_parcels()


async def test_a_tracking_page_with_nothing_on_it_warns_once(caplog):
    session = _session(_active_tile("SHIPactive1"))
    _shipment(
        session, "SHIPactive1", tracking_id=None, carrier=None, state=False
    )
    client = _client(session)

    await client.async_get_parcels()
    await client.async_get_parcels()

    assert caplog.text.count("held no tracking id") == 1
    assert "issues/new" in caplog.text


async def test_undispatched_order_without_a_shipment_becomes_a_registered_parcel():
    from custom_components.amazon_orders.account.parcels import normalize_parcel

    undispatched = (
        '<a class="item-card__link" '
        'href="/-/en/your-orders/pop?orderId=000-0000002-0000002&amp;lineItemId=lineitem0009" '
        'aria-label="Widget, Order placed 2 October 2026, Not Yet Dispatched"></a>'
    )
    session = _session(undispatched)
    session.add("GET", "lineItemId=lineitem0009", FakeResponse(200, pop_page(None)))

    (record,) = await _client(session).async_get_parcels()
    parcel = normalize_parcel(record)

    assert parcel["status"].value == "registered"
    assert parcel["barcode"] == "000-0000002-0000002-lineitem0009"
    assert record["barcode_source"] == "shipment_key"
