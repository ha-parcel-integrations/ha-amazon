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
import logging
from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any

import aiohttp
from yarl import URL

from ..const import (
    DELIVERED_LOOKBACK_DAYS,
    MAX_TRACK_LOADS,
    ORDERS_PATHS,
    REQUEST_PAUSE_SECONDS,
)
from .auth import exchange_token_for_cookies, refresh_access_token
from .errors import AmazonApiError, AmazonAuthError
from .pages import (
    OrderTile,
    build_record,
    looks_signed_out,
    parse_order_tiles,
    parse_track_link,
    parse_track_page,
)
from .parcels import warn_once

_LOGGER = logging.getLogger(__name__)

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

    async def _fetch_page(self, path: str) -> str:
        """GET a signed-in page, mapping every failure mode to our errors."""
        if self._requests:
            await asyncio.sleep(REQUEST_PAUSE_SECONDS)
        self._requests += 1
        url = path if path.startswith("http") else f"https://www.{self._domain}{path}"
        async with self._session.get(
            url, headers={"User-Agent": WEB_USER_AGENT}
        ) as response:
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
                tiles = parse_order_tiles(await self._fetch_page(path), today)
            except AmazonAuthError:
                raise
            except AmazonApiError as err:
                if err.status_code in (429, 503):
                    raise
                failure = err
                continue
            if tiles:
                return tiles
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

    async def _read_shipment(
        self, tiles: list[OrderTile], today: date
    ) -> dict[str, Any]:
        """Follow the order line to its tracking page and build the record."""
        pop_page = await self._fetch_page(tiles[0].pop_path)
        track_path = parse_track_link(pop_page)
        track = None
        if track_path:
            track = parse_track_page(await self._fetch_page(track_path), today)
            if track.tracking_id is None and not track.events:
                warn_once(
                    "empty-track-page",
                    "A shipment tracking page held no tracking id and no timeline;"
                    " Amazon may have changed the page.",
                )
        return build_record(self._domain, tiles, track_path, track)

    @staticmethod
    def _is_delivered(tiles: list[OrderTile], record: dict[str, Any]) -> bool:
        return tiles[0].delivered or record.get("milestone") == "DELIVERED"

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
            if self._is_delivered(groups[key], record):
                self._final[key] = record

        records: list[dict[str, Any]] = []
        for key in groups:
            if key in self._final:
                records.append(self._final[key])
            elif key in self._last:
                records.append(self._last[key][0])
        return records
