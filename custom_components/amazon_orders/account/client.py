"""Amazon account client: sign in from the stored token, read order pages.

Each poll renews the access token, exchanges the refresh token for fresh
website cookies, reads the orders page and follows the order lines that are
still moving. The contract the coordinator relies on:

* :class:`AmazonAuthError` only when Amazon no longer accepts the stored
  sign-in (a rejected token, a redirect to a sign-in page, a password form) —
  an outage must never push users into a sign-in they cannot complete,
* ``async_get_parcels`` returns one raw record per shipment,
* ``aiohttp.ClientError`` propagates untouched.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any

import aiohttp
from homeassistant.util import dt as dt_util
from yarl import URL

from ..const import (
    DELIVERED_LOOKBACK_DAYS,
    MAX_TRACK_LOADS,
    ORDERS_PATHS,
    REQUEST_PAUSE_SECONDS,
    ParcelStatus,
)
from .auth import exchange_token_for_cookies, refresh_access_token
from .errors import AmazonApiError, AmazonAuthError
from .pages import (
    BUSINESS_HISTORY_PATH,
    OrderTile,
    build_record,
    business_card_count,
    business_history_failed,
    is_track_page_path,
    looks_signed_out,
    page_timezone,
    parse_business_cards,
    parse_business_shell,
    parse_order_tiles,
    parse_track_link,
    parse_track_page,
)
from .parcels import _delivered_at, warn_once
from .vocabulary import classify_order_text

_LOGGER = logging.getLogger(__name__)

# suite: allow browser user-agent (the order pages are the website, scraped as a mobile browser)
WEB_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

_ShipmentKey = tuple[str, str, str]


class AmazonClient:
    """Reads one Amazon storefront on behalf of one config entry."""

    def __init__(
        self, domain: str, refresh_token: str, session: aiohttp.ClientSession
    ) -> None:
        """Initialise with the storefront, stored token and per-entry session."""
        self._domain = domain
        self._refresh_token = refresh_token
        self._session = session
        self._api_host: str | None = None
        self._requests = 0
        # Shipments read in full once delivered are never fetched again.
        self._final: dict[_ShipmentKey, dict[str, Any]] = {}
        # Last record and read time per shipment, so a capped cycle can
        # rotate through the rest without a barcode flipping in between.
        self._last: dict[_ShipmentKey, tuple[dict[str, Any], datetime]] = {}
        # Day a shipment was first read as delivered without a delivery date.
        self._undated: dict[_ShipmentKey, date] = {}
        # Business: a delivered card drops its shipment and tracking link, so
        # the in-flight card's are kept to carry the parcel over.
        self._business_lines: dict[tuple[str, str], tuple[str, str]] = {}

    async def _authenticate(self) -> None:
        """Renew the access token and mint fresh website cookies."""
        self._api_host, _ = await refresh_access_token(
            self._session,
            self._domain,
            self._refresh_token,
            preferred_host=self._api_host,
        )
        cookies = await exchange_token_for_cookies(
            self._session, self._domain, self._refresh_token
        )
        jar = self._session.cookie_jar
        for item in cookies:
            cookie: SimpleCookie = SimpleCookie()
            name = item["Name"]
            cookie[name] = str(item["Value"]).strip('"')
            cookie[name]["domain"] = item["domain"]
            cookie[name]["path"] = item.get("Path") or "/"
            cookie[name]["secure"] = bool(item.get("Secure", True))
            jar.update_cookies(cookie, URL(f"https://www.{self._domain}/"))

    async def _fetch_page(
        self,
        path: str,
        *,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Load one signed-in page (a POST with ``form``), mapping every failure."""
        if self._requests:
            await asyncio.sleep(REQUEST_PAUSE_SECONDS)
        self._requests += 1
        url = path if path.startswith("http") else f"https://www.{self._domain}{path}"
        sent = {"User-Agent": WEB_USER_AGENT, **(headers or {})}
        request = (
            self._session.post(url, data=form, headers=sent)
            if form is not None
            else self._session.get(url, headers=sent)
        )
        async with request as response:
            status = response.status
            if status in (401, 403):
                raise AmazonAuthError(f"HTTP {status}", status_code=status)
            if status in (429, 503):
                header = response.headers.get("Retry-After")
                try:
                    retry_after = float(header) if header else None
                except ValueError:
                    retry_after = None  # an HTTP-date; the coordinator backs off itself
                raise AmazonApiError(
                    f"HTTP {status}", status_code=status, retry_after=retry_after
                )
            if status != 200:
                raise AmazonApiError(f"HTTP {status}", status_code=status)
            landed_on = response.url.path
            page = await response.text()
        if "/ap/signin" in landed_on or looks_signed_out(page):
            raise AmazonAuthError("redirected to sign-in")
        return page

    async def _fetch_tiles(self, today: date) -> list[OrderTile]:
        """Read the orders page, falling back to the legacy history path."""
        failure: AmazonApiError | None = None
        for path in ORDERS_PATHS:
            try:
                page = await self._fetch_page(path)
            except AmazonAuthError:
                raise
            except AmazonApiError as err:
                if err.status_code in (429, 503):
                    raise
                failure = err
                continue
            if tiles := parse_order_tiles(page, today):
                return tiles
            if parse_business_shell(page) is not None:
                return await self._fetch_business_tiles(path, page, today)
        if failure is not None:
            raise failure
        # An account with no recent orders gets here legitimately; the same
        # outcome after a layout change would otherwise be silent.
        warn_once(
            "no-order-lines",
            "No order lines were recognised on the Amazon orders page. If you"
            " have recent orders, Amazon may have changed the page.",
        )
        return []

    async def _fetch_business_tiles(
        self, path: str, shell: str, today: date
    ) -> list[OrderTile]:
        """Read an Amazon Business order list, which the orders page loads itself."""
        try:
            fragment = await self._post_business(shell)
        except AmazonAuthError as err:
            if err.status_code != 403:
                raise
            # A stale anti-forgery token: a fresh shell carries a new one.
            fragment = await self._post_business(await self._fetch_page(path))
        if business_history_failed(fragment):
            raise AmazonApiError("Amazon Business order list reported an error")
        tiles = [
            self._carry_business_line(tile)
            for tile in parse_business_cards(fragment, today)
        ]
        # An empty list may be a silent failure; it must not forget anything.
        if tiles:
            listed = {(tile.order_id, tile.title) for tile in tiles}
            self._business_lines = {
                line: known
                for line, known in self._business_lines.items()
                if line in listed
            }
        if not tiles and not business_card_count(fragment):
            warn_once(
                "no-business-lines",
                "The Amazon Business order list came back empty. If you have"
                " orders from the last three months, Amazon may have changed"
                " how the list is requested.",
            )
        return tiles

    async def _post_business(self, shell: str) -> str:
        request = parse_business_shell(shell)
        if request is None:
            raise AmazonApiError("Amazon Business orders page changed")
        origin = f"https://www.{self._domain}"
        return await self._fetch_page(
            BUSINESS_HISTORY_PATH,
            form=request.form,
            headers={
                "Accept": "text/html,*/*",
                "X-Requested-With": "XMLHttpRequest",
                "anti-csrftoken-a2z": request.token,
                "Origin": origin,
                "Referer": f"{origin}{ORDERS_PATHS[0]}",
            },
        )

    @property
    def business_lines(self) -> list[list[str]]:
        """The remembered Business lines, in a shape that can be stored."""
        return [
            [order, title, shipment, path]
            for (order, title), (shipment, path) in self._business_lines.items()
        ]

    def restore_business_lines(self, lines: list[list[str]]) -> None:
        """Take back the Business lines remembered before a restart."""
        self._business_lines = {
            (line[0], line[1]): (line[2], line[3])
            for line in lines
            if isinstance(line, list) and len(line) == 4
        }

    def _carry_business_line(self, tile: OrderTile) -> OrderTile:
        """Keep a Business parcel's shipment once its card reads as delivered."""
        line = (tile.order_id, tile.title)
        if tile.shipment_id:
            self._business_lines[line] = (tile.shipment_id, tile.pop_path)
            return tile
        known = self._business_lines.get(line)
        if known is None:
            return tile
        return dataclasses.replace(
            tile, shipment_id=known[0], pop_path=known[1], follow=True
        )

    async def _read_shipment(
        self, tiles: list[OrderTile], today: date
    ) -> dict[str, Any]:
        """Follow the order line to its tracking page and build the record."""
        lead_path = tiles[0].pop_path
        track_path: str | None = None
        track_page: str | None = None
        if not tiles[0].follow:
            pass  # A Business order page, which has no tracking on it.
        elif is_track_page_path(lead_path):
            # amazon.fr links an in-flight line straight to its tracking page.
            track_path = lead_path
            track_page = await self._fetch_page(lead_path)
        else:
            track_path = parse_track_link(await self._fetch_page(lead_path))
            if track_path:
                track_page = await self._fetch_page(track_path)
        track = None
        if track_page is not None:
            # Load the page's zone off the event loop; the parser then finds
            # it already cached.
            if zone_name := page_timezone(track_page):
                await dt_util.async_get_time_zone(zone_name)
            track = parse_track_page(track_page, today)
            # A line not yet dispatched has neither, legitimately.
            if (
                track.tracking_id is None
                and not track.events
                and track.state.get("shortStatus") != "ORDER_PLACED"
            ):
                warn_once(
                    "empty-track-page",
                    "A shipment tracking page held no tracking id and no timeline;"
                    " Amazon may have changed the page.",
                )
        elif classify_order_text(tiles[0].status_text)[0] in (
            ParcelStatus.IN_TRANSIT,
            ParcelStatus.OUT_FOR_DELIVERY,
        ):
            warn_once(
                "no-track-link",
                "A shipped Amazon order line linked to no tracking page we could"
                " find, so its carrier, tracking id and timeline are missing;"
                " Amazon may use a different tracking page on this storefront.",
            )
        return build_record(
            self._domain, tiles, track_path, track, read_at=datetime.now(timezone.utc)
        )

    def _is_final(
        self,
        key: _ShipmentKey,
        tiles: list[OrderTile],
        record: dict[str, Any],
        today: date,
    ) -> bool:
        """Whether a shipment is delivered and needs no further reads.

        The milestone can say delivered before the order line carries the
        delivery date; reading it once more the next day picks that date up.
        """
        if not (tiles[0].delivered or record.get("milestone") == "DELIVERED"):
            return False
        if _delivered_at(record) is not None:
            return True
        return self._undated.setdefault(key, today) < today

    async def async_get_parcels(self) -> list[dict[str, Any]]:
        """Return one raw record per shipment worth showing."""
        await self._authenticate()
        today = date.today()
        tiles = await self._fetch_tiles(today)

        groups: dict[_ShipmentKey, list[OrderTile]] = {}
        for tile in tiles:
            groups.setdefault(tile.key, []).append(tile)
        self._final = {k: v for k, v in self._final.items() if k in groups}
        self._last = {k: v for k, v in self._last.items() if k in groups}
        self._undated = {k: v for k, v in self._undated.items() if k in groups}

        cutoff = today - timedelta(days=DELIVERED_LOOKBACK_DAYS)
        work: list[_ShipmentKey] = []
        for key, group in groups.items():
            lead = group[0]
            if key in self._final:
                continue
            if lead.delivered and lead.delivered_on and lead.delivered_on < cutoff:
                continue
            work.append(key)

        never = datetime.min.replace(tzinfo=timezone.utc)
        work.sort(key=lambda key: self._last[key][1] if key in self._last else never)
        for key in work[:MAX_TRACK_LOADS]:
            record = await self._read_shipment(groups[key], today)
            self._last[key] = (record, datetime.now(timezone.utc))
            if self._is_final(key, groups[key], record, today):
                self._final[key] = record
                self._undated.pop(key, None)

        records: list[dict[str, Any]] = []
        for key in groups:
            if key in self._final:
                records.append(self._final[key])
            elif key in self._last:
                records.append(self._last[key][0])
        return records
