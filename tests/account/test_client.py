"""Tests for the Amazon page client: auth, fan-out caps and failure mapping."""

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.amazon_orders.account import client as client_module
from custom_components.amazon_orders.account.client import AmazonClient
from custom_components.amazon_orders.account.errors import (
    AmazonApiError,
    AmazonAuthError,
)
from custom_components.amazon_orders.account.pages import business_cards_found
from custom_components.amazon_orders.account.parcels import normalize_parcel

from ..fakes import FakeResponse, FakeSession
from ..payloads import (
    ACTIVE_CODE,
    BUSINESS_TOKEN,
    COUNTRY,
    DELIVERED_CODE,
    DELIVERED_EVENTS,
    FEEDBACK_PATH,
    ORDER_ID,
    business_card,
    business_history,
    business_order_path,
    business_shell,
    business_tracker_path,
    orders_page,
    pop_page,
    progress_tracker_path,
    tile,
    track_page,
    track_path,
)

COOKIES = {
    "response": {
        "tokens": {
            "cookies": {
                f".{COUNTRY}": [
                    {
                        "Name": "session-id",
                        "Value": '"abc"',
                        "Path": "/",
                        "Secure": True,
                    },
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


async def test_a_delivered_milestone_without_a_date_is_read_again_the_next_day():
    session = _session(_active_tile("SHIPdone001"))
    _shipment(session, "SHIPdone001", events=[], milestone="DELIVERED")
    client = _client(session)

    await client.async_get_parcels()
    await client.async_get_parcels()
    assert sum("ship-track" in url for url in _gets(session)) == 2

    (key,) = client._undated
    client._undated[key] = client_module.dt_util.now().date() - timedelta(days=1)
    await client.async_get_parcels()
    await client.async_get_parcels()
    assert sum("ship-track" in url for url in _gets(session)) == 3
    assert client._undated == {}


async def test_a_parcel_collected_from_a_locker_is_cached_too():
    session = _session(_active_tile("SHIPlocker1"))
    _shipment(
        session,
        "SHIPlocker1",
        milestone=None,
        short_status="PICKED_UP",
        events=DELIVERED_EVENTS,
    )
    client = _client(session)

    await client.async_get_parcels()
    await client.async_get_parcels()
    assert sum("ship-track" in url for url in _gets(session)) == 1


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


async def test_a_shipped_line_without_a_tracking_link_warns_once(caplog):
    session = _session(_active_tile("SHIPnotrack1"), _delivered_tile("SHIPnotrack2"))
    _shipment(session, "SHIPnotrack1", tracked=False)
    _shipment(session, "SHIPnotrack2", tracked=False)
    client = _client(session)

    await client.async_get_parcels()
    await client.async_get_parcels()

    assert caplog.text.count("linked to no tracking page") == 1


async def test_delivered_shipment_without_a_tracking_page_is_final_too():
    session = _session(_delivered_tile("SHIPnotrack1"))
    _shipment(session, "SHIPnotrack1", tracked=False)
    client = _client(session)

    await client.async_get_parcels()
    await client.async_get_parcels()

    assert sum("pop?" in url for url in _gets(session)) == 1


async def test_a_line_linking_straight_to_its_tracking_page_costs_one_read():
    path = progress_tracker_path("SHIPfr00001")
    session = _session(
        tile(
            "SHIPfr00001",
            "Arrive aujourd'hui dans la tranche horaire suivante : 14:00 - 16:30",
            None,
            placed_anchor="Commande effectuée le",
            placed=_placed(1),
            href=path,
        )
    )
    session.add(
        "GET",
        "progress-tracker/package?",
        FakeResponse(
            200,
            track_page(
                tracking_id=ACTIVE_CODE,
                carrier_header="Livraison par Amazon",
                tracking_label="Numéro de suivi :",
                milestone="OUT_FOR_DELIVERY",
                short_status="OUT_FOR_DELIVERY",
                timezone="Europe/Paris",
                events=[
                    (
                        "samedi 24 octobre",
                        "13:51",
                        "En cours de livraison",
                        "Ville, FR",
                    ),
                    (
                        "samedi 24 octobre",
                        "6:03",
                        "Le colis est arrivé à l'agence de livraison finale\n",
                        "Ville, FR",
                    ),
                    (
                        "vendredi 23 octobre",
                        "",
                        "Le colis a quitté les locaux de l'expéditeur",
                        "",
                    ),
                ],
            ),
        ),
    )

    (record,) = await _client(session).async_get_parcels()

    assert _gets(session)[1:] == [f"https://www.{COUNTRY}{path}"]
    assert record["track_path"] == path
    parcel = normalize_parcel(record, include_history=True)
    assert parcel["barcode"] == ACTIVE_CODE
    assert parcel["carrier"] == "Amazon"
    assert parcel["status"] == "out_for_delivery"
    assert [entry["status"] for entry in parcel["history"]] == [
        "in_transit",
        "in_transit",
        "out_for_delivery",
    ]


async def test_a_delivered_line_skips_the_feedback_survey_for_ship_track():
    path = track_path("SHIPfr00002")
    session = _session(
        tile(
            "SHIPfr00002",
            f"Livré : {_day(1)}",
            None,
            placed_anchor="Commande effectuée le",
            placed=_placed(3),
        )
    )
    session.add(
        "GET",
        "pop?",
        FakeResponse(
            200,
            f'<html><body><a href="{FEEDBACK_PATH}">Avis</a>'
            f'<a href="{path}">Suivre</a></body></html>',
        ),
    )
    session.add(
        "GET",
        "ship-track?",
        FakeResponse(
            200,
            track_page(
                tracking_id=DELIVERED_CODE,
                carrier_header="Livraison par Amazon",
                short_status="DELIVERED",
                events=[("samedi 24 octobre", "15:40", "Livré", "Ville, FR")],
            ),
        ),
    )

    (record,) = await _client(session).async_get_parcels()

    gets = _gets(session)
    assert len(gets) == 3
    assert "ship-track" in gets[2]
    assert not any("delivery-feedback" in url for url in gets)
    assert normalize_parcel(record)["status"] == "delivered"


async def test_a_not_yet_dispatched_tracking_page_is_not_reported_empty(caplog):
    path = progress_tracker_path("SHIPfr00007")
    session = _session(
        tile(
            "SHIPfr00007",
            "Livraison prévue mardi",
            None,
            placed_anchor="Commande effectuée le",
            placed=_placed(1),
            href=path,
        )
    )
    session.add(
        "GET",
        "progress-tracker/package?",
        FakeResponse(
            200,
            track_page(
                tracking_id=None,
                carrier=None,
                milestone="ORDERED",
                short_status="ORDER_PLACED",
                timezone="Europe/Paris",
            ),
        ),
    )

    (record,) = await _client(session).async_get_parcels()

    assert normalize_parcel(record)["status"] == "registered"
    assert "held no tracking id" not in caplog.text


async def test_order_lines_of_one_shipment_cost_one_read():
    session = _session(
        _active_tile("SHIPactive1", line_item="lineitem0001"),
        _active_tile("SHIPactive1", line_item="lineitem0002", title="Second item"),
    )
    _shipment(session, "SHIPactive1", milestone="IN_PROGRESS")

    (record,) = await _client(session).async_get_parcels()

    assert record["items"] == ["Example Widget, Blue", "Second item"]
    assert sum("pop?" in url for url in _gets(session)) == 1


async def test_today_is_home_assistant_s_day_not_the_os_s(monkeypatch):
    # 23:30 UTC is already the next day in Amsterdam.
    monkeypatch.setattr(
        client_module.dt_util,
        "now",
        lambda: datetime(2026, 10, 5, 23, 30, tzinfo=timezone.utc).astimezone(
            ZoneInfo("Europe/Amsterdam")
        ),
    )
    seen: list[date] = []

    async def fetch_tiles(today):
        seen.append(today)
        return []

    client = AmazonClient("amazon.nl", "refresh", _session())
    monkeypatch.setattr(client, "_fetch_tiles", fetch_tiles)
    assert await client.async_get_parcels() == []
    assert seen == [date(2026, 10, 6)]


async def test_pause_between_page_loads(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(client_module, "REQUEST_PAUSE_SECONDS", 1.5)
    session = _session(_active_tile("SHIPactive1"))
    _shipment(session, "SHIPactive1", milestone="IN_PROGRESS")

    client = _client(session)
    await client.async_get_parcels()
    assert sleeps == [1.5, 1.5]  # before pop and before track, not before orders

    # The next poll starts without a pause again.
    sleeps.clear()
    await client.async_get_parcels()
    assert sleeps == [1.5, 1.5]


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
    "headers,expected",
    [({"Retry-After": "90"}, 90.0), ({"Retry-After": "Wed, 1 Jan"}, None), ({}, None)],
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
    _shipment(session, "SHIPactive1", tracking_id=None, carrier=None, state=False)
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


async def test_no_recognised_order_lines_warns_once(caplog):
    session = _session()
    client = _client(session)

    assert await client.async_get_parcels() == []
    assert await client.async_get_parcels() == []

    assert caplog.text.count("No order lines were recognised") == 1


async def test_a_failing_orders_page_does_not_claim_the_layout_changed(caplog):
    session = _session(orders_status=500)
    session.add("GET", "/gp/css/order-history", FakeResponse(500, ""))
    with pytest.raises(AmazonApiError):
        await _client(session).async_get_parcels()
    assert "No order lines" not in caplog.text


# ---------------------------------------------------------------------------
# Amazon Business
# ---------------------------------------------------------------------------


def _business_session(*fragments) -> FakeSession:
    """Script the Business list; a page with cards is followed by the empty one that ends it."""
    session = _session()
    session.add("GET", "/your-orders/orders", FakeResponse(200, business_shell()))
    pages = []
    for response in fragments:
        pages.append(response)
        if response.status == 200 and business_cards_found(response._body):
            pages.append(FakeResponse(200, business_history()))
    session.add("POST", "/ab/your-orders/deliveryHistory", *pages)
    return session


def _business_track(session: FakeSession, shipment: str, **page) -> None:
    session.add(
        "GET",
        f"shipmentId={shipment}&vt=YOUR_ORDERS",
        FakeResponse(200, track_page(**page)),
    )


def _posts(session: FakeSession) -> list[tuple[str, dict]]:
    return [
        (url, kwargs)
        for method, url, kwargs in session.calls
        if method == "POST" and "deliveryHistory" in url
    ]


async def test_a_business_account_reads_its_order_list_then_the_tracking_page():
    session = _business_session(
        FakeResponse(
            200,
            business_history(
                business_card(
                    business_tracker_path("SHIPab00001"), "Livraison prévue lundi"
                ),
                business_card("/dp/B000TEST00/ref=x", "Annulée", cancelled=True),
            ),
        )
    )
    _business_track(
        session,
        "SHIPab00001",
        tracking_id="6A00000000001",
        carrier_header="Informations sur la livraison",
        milestone="SHIPPED",
        short_status="IN_TRANSIT",
    )

    (record,) = await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()

    (url, kwargs), (_, end) = _posts(session)
    # The list ends on the first page with no card.
    assert json.loads(end["data"]["deliveryHistoryRequestString"])["pageNumber"] == 1
    assert url == "https://www.amazon.fr/ab/your-orders/deliveryHistory"
    assert kwargs["headers"]["anti-csrftoken-a2z"] == BUSINESS_TOKEN
    assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert "iPhone" in kwargs["headers"]["User-Agent"]
    assert '"searchKeyword": null' in kwargs["data"]["deliveryHistoryRequestString"]
    # The orders page is not read a second time through the legacy path.
    assert not any("order-history" in url for url in _gets(session))
    parcel = normalize_parcel(record)
    assert parcel["status"] == "in_transit"
    assert parcel["barcode"] == "6A00000000001"
    assert parcel["carrier"] == "La Poste"


async def test_a_delivered_business_card_stays_the_same_parcel():
    session = _business_session(
        FakeResponse(
            200,
            business_history(
                business_card(
                    business_tracker_path("SHIPab00001"), "Livraison prévue lundi"
                )
            ),
        ),
        FakeResponse(
            200,
            business_history(business_card(business_order_path(), "Livré&nbsp;: hier")),
        ),
    )
    _business_track(
        session, "SHIPab00001", milestone="SHIPPED", short_status="IN_TRANSIT"
    )
    client = AmazonClient("amazon.fr", "refresh", session)

    (first,) = await client.async_get_parcels()
    _business_track(
        session, "SHIPab00001", milestone="DELIVERED", short_status="DELIVERED"
    )
    (second,) = await client.async_get_parcels()

    assert normalize_parcel(second)["barcode"] == normalize_parcel(first)["barcode"]
    assert normalize_parcel(second)["status"] == "delivered"
    assert second["track_path"] == business_tracker_path("SHIPab00001")


async def test_a_business_card_already_delivered_costs_no_extra_read():
    session = _business_session(
        FakeResponse(
            200, business_history(business_card(business_order_path(), "Livré jeudi"))
        )
    )

    (record,) = await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()

    assert not any("/gp/aw/ya" in url for url in _gets(session))
    assert normalize_parcel(record)["status"] == "delivered"
    assert record["delivered_on"] is not None


async def test_a_stale_business_token_is_renewed_once():
    session = _business_session(
        FakeResponse(403),
        FakeResponse(
            200,
            business_history(business_card(business_order_path(), "Livré jeudi")),
        ),
    )

    records = await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()

    assert len(records) == 1
    # Refused, renewed, then the page and the empty one after it.
    assert len(_posts(session)) == 3
    assert sum(url.endswith("/your-orders/orders") for url in _gets(session)) == 2


async def test_a_business_token_refused_twice_is_an_auth_error():
    session = _business_session(FakeResponse(403))
    with pytest.raises(AmazonAuthError):
        await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()
    assert len(_posts(session)) == 2


async def test_an_other_business_auth_failure_is_not_retried():
    session = _business_session(FakeResponse(401))
    with pytest.raises(AmazonAuthError):
        await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()
    assert len(_posts(session)) == 1


async def test_a_business_list_error_fails_the_poll():
    session = _business_session(FakeResponse(200, business_history(exception=True)))
    with pytest.raises(AmazonApiError):
        await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()


async def test_a_renewed_shell_that_is_no_longer_business_fails_the_poll():
    session = _business_session(FakeResponse(403))
    session.add(
        "GET",
        "/your-orders/orders",
        FakeResponse(200, business_shell()),
        FakeResponse(200, orders_page()),
    )
    with pytest.raises(AmazonApiError):
        await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()


async def test_an_empty_business_list_warns_once(caplog):
    session = _business_session(FakeResponse(200, business_history()))
    client = AmazonClient("amazon.fr", "refresh", session)
    assert await client.async_get_parcels() == []
    assert await client.async_get_parcels() == []
    assert caplog.text.count("Amazon Business order list came back empty") == 1


async def test_business_cards_the_list_could_not_read_warn_once(caplog):
    fragment = business_history(
        business_card(business_order_path(), "Livré&nbsp;: hier"), count=2
    )
    session = _business_session(
        FakeResponse(200, fragment), FakeResponse(200, fragment)
    )
    client = AmazonClient("amazon.fr", "refresh", session)
    assert len(await client.async_get_parcels()) == 1
    assert caplog.text.count("more Business order cards than could be read") == 1
    assert "came back empty" not in caplog.text


async def test_a_business_list_of_only_cancelled_cards_does_not_warn(caplog):
    session = _business_session(
        FakeResponse(
            200,
            business_history(
                business_card("/dp/B000TEST00/ref=x", "Annulée", cancelled=True)
            ),
        )
    )
    assert await AmazonClient("amazon.fr", "refresh", session).async_get_parcels() == []
    assert "came back empty" not in caplog.text


async def test_business_lines_survive_a_restart_and_a_silent_empty_list():
    session = _business_session(
        FakeResponse(200, business_history()),
        FakeResponse(
            200,
            business_history(business_card(business_order_path(), "Livré&nbsp;: hier")),
        ),
    )
    _business_track(
        session, "SHIPab00001", milestone="DELIVERED", short_status="DELIVERED"
    )
    before = AmazonClient("amazon.fr", "refresh", session)
    before.restore_business_lines(
        [
            [
                ORDER_ID,
                "Example item 1",
                "SHIPab00001",
                business_tracker_path("SHIPab00001"),
            ]
        ]
    )
    stored = before.business_lines
    after = AmazonClient("amazon.fr", "refresh", session)
    after.restore_business_lines([*stored, ["not", "a", "line"], "junk"])

    assert await after.async_get_parcels() == []
    assert after.business_lines == stored
    (record,) = await after.async_get_parcels()

    assert record["shipment_id"] == "SHIPab00001"
    assert record["tracking_id"] == ACTIVE_CODE


def _page_numbers(session: FakeSession) -> list[int]:
    return [
        json.loads(kwargs["data"]["deliveryHistoryRequestString"])["pageNumber"]
        for _, kwargs in _posts(session)
    ]


async def test_a_long_business_list_is_read_page_by_page():
    session = _session()
    session.add("GET", "/your-orders/orders", FakeResponse(200, business_shell()))
    session.add(
        "POST",
        "/ab/your-orders/deliveryHistory",
        FakeResponse(
            200,
            business_history(
                business_card(
                    business_order_path("000-0000006-0000001"), "Livré jeudi"
                ),
                count=1,
            ),
        ),
        # An older order still on its way, on the second page.
        FakeResponse(
            200,
            business_history(
                business_card(
                    business_tracker_path("SHIPab00002"), "Livraison prévue lundi"
                ),
                count=1,
            ),
        ),
        FakeResponse(200, business_history()),
    )
    _business_track(
        session, "SHIPab00002", milestone="SHIPPED", short_status="IN_TRANSIT"
    )

    records = await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()

    assert _page_numbers(session) == [0, 1, 2]
    assert sorted(normalize_parcel(r)["status"] for r in records) == [
        "delivered",
        "in_transit",
    ]


async def test_the_business_list_stops_at_the_page_cap(monkeypatch, caplog):
    monkeypatch.setattr(client_module, "MAX_BUSINESS_PAGES", 2)
    session = _session()
    session.add("GET", "/your-orders/orders", FakeResponse(200, business_shell()))
    session.add(
        "POST",
        "/ab/your-orders/deliveryHistory",
        FakeResponse(
            200, business_history(business_card(business_order_path(), "Livré jeudi"))
        ),
    )
    client = AmazonClient("amazon.fr", "refresh", session)

    await client.async_get_parcels()
    await client.async_get_parcels()

    assert _page_numbers(session) == [0, 1, 0, 1]
    assert caplog.text.count("longer than 2 pages") == 1


async def test_a_business_page_without_a_counter_ends_on_no_card():
    session = _session()
    session.add("GET", "/your-orders/orders", FakeResponse(200, business_shell()))
    without_counter = business_history(
        business_card(business_order_path(), "Livré jeudi")
    ).replace('data-noOfDeliveryCardsRendered="1"', "")
    session.add(
        "POST",
        "/ab/your-orders/deliveryHistory",
        FakeResponse(200, without_counter),
        FakeResponse(
            200, business_history().replace('data-noOfDeliveryCardsRendered="0"', "")
        ),
    )

    records = await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()

    assert len(records) == 1
    assert _page_numbers(session) == [0, 1]


async def test_an_empty_page_after_the_first_does_not_warn(caplog):
    session = _business_session(
        FakeResponse(
            200, business_history(business_card(business_order_path(), "Livré jeudi"))
        )
    )
    await AmazonClient("amazon.fr", "refresh", session).async_get_parcels()
    assert "came back empty" not in caplog.text
