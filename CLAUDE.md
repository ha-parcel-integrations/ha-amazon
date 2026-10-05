# Working in this repository

Home Assistant custom integration for **Amazon** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

API mechanics — endpoints, parameters, status vocabularies — live in the
private `carrier-research/amazon-logistics/api/` and are **never** copied here.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `tests/account/test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest. A field you could not verify (no real parcel yet) goes in `PENDING_CAPABILITIES` instead of being left out — the site then says "awaiting data" rather than claiming the API never exposes it; move it into `CAPABILITIES` once confirmed |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **The optional pickup-point summaries are a pair.** A carrier that can tell
  a parcel is destined for a pickup point exposes
  `en_route_to_pickup_point` for `pickup is true` before it arrives; one that
  can reach `ParcelStatus.AT_PICKUP_POINT` also exposes `awaiting_pickup`.
  See *Parcel contract* in `CONVENTIONS.md`. Say "pickup point", not
  "ServicePoint"/"parcel shop"/"locker", for the generic concept; the
  example carrier demonstrates both canonical sensors.

## Carrier-specific notes

**Build-time gate, still open.** The research gate "does the refresh token
survive several idle days" had not been answered when this repo was built; the
maintainer chose to build ahead of it. If the stored sign-in dies within days
the integration would need a re-login every week and should not ship, so
resolve that before any tag, release or public push. Until then the repo is
local only.

Account model, but with an HTML source: the carrier-research standing ruling
permits parsing signed-in pages for Amazon **only** (a named exception, not a
precedent for any other carrier). Prefer the embedded JSON state on a page over
its visible text.

- **One config entry per Amazon country** (`unique_id` = the storefront
  domain, e.g. `amazon.nl`); `single_config_entry` is deliberately not set.
- **Sign-in is a pasted link, not a password form.** The flow shows a sign-in
  URL, the user completes it in their own browser and pastes the final address
  back; the flow extracts the authorization code from it. Entry data holds the
  storefront, the refresh token and the device serial, nothing else. All three
  are redacted in diagnostics; the PKCE verifier lives only in the flow.
- **Every poll** renews the access token, exchanges the refresh token for
  fresh website cookies into the per-entry cookie jar, then reads the orders
  page. A 4xx on refresh or exchange, a redirect to a sign-in page, or a
  password form in any page raises `AmazonAuthError` -> `ConfigEntryAuthFailed`
  (one reauth, polling pauses). Never fall back to a password login. 429 and
  503 back off through the coordinator like any other carrier.
- **Fan-out is capped.** Orders page, then each shipment's order-line page,
  then its ship-track page when "Track package" exists. At most
  `MAX_TRACK_LOADS` shipments are read per cycle (stalest first, one request at
  a time with a pause between), shipments delivered more than
  `DELIVERED_LOOKBACK_DAYS` ago are not followed, and a delivered shipment is
  read once and then served from memory. The `HOT`/`MID` tiers are the suite's;
  the **idle tier** (`IDLE_INTERVAL_MINUTES`, nothing in flight) is a local
  divergence because every poll costs a token refresh and a cookie exchange.
- **Barcode** is the ship-track page's tracking id. Shipments with no tracking
  page (4 of 6 order lines in the capture; cause unknown) fall back to the
  shipment key from the order-line URL and say so in `raw["barcode_source"]`;
  the key flips to the tracking id if one shows up later.
- **Status resolution** (`resolve_status`): progress-tracker milestone, else the
  newest mappable timeline event, else the order line's status text, else
  `unknown` + one warning. Only the `DELIVERED` milestone has been observed;
  do not add milestone names that no capture has shown.
- **Fields that are `None` on purpose:** `sender`, `receiver`, `weight`,
  `dimensions`, `pickup_point`. `planned_from`/`planned_to` are
  `PENDING_CAPABILITIES` (`delivery_window`): no parseable expected-delivery
  date has been seen yet, so move it into `CAPABILITIES` only once a real
  in-flight parcel shows one. `url` is the ship-track page, else the order-line
  page.
- **`raw` carries the whole shipment record** (the parsed page content, item
  titles, ids and the embedded page state). The embedded page state's
  third-party map key is dropped at parse time because it is not parcel data.
  Privacy is the diagnostics redaction's job only.
- **No outgoing parcels:** Amazon's signed-in pages only list what the user
  ordered, so there is no consumer-sending surface to split on.
- **Not built:** the data.<domain> deliveries JSON (404 with this token),
  browser automation, IMAP parsing, order-level sensors, alexapy as a
  requirement, and routing a DHL-carried parcel on to `ha-dhl`. The routing hint
  from the research (a DHL tracking id inside an Amazon order) is only exposed
  as `carrier` + `raw["carrier_code"]`; there is no cross-integration hand-off
  mechanism to build it on yet, so the aggregator-side de-duplication by
  barcode is the place for it.
- **Attribution:** `account/auth.py` is adapted from alexapy (Apache-2.0).
  Keep the module docstring, `NOTICE`, `LICENSE-Apache-2.0` and the README
  Credits section in step; credit alexapy in the commit that touches that
  code. The docs-site carrier entry still owes the same one-line credit.
- Watch alexapy releases for changes to registration, refresh or exchange;
  Amazon protocol fixes usually land there first.


## Options and reloads

For code-based carriers, the options flow starts with exactly `Parcels` and
`Settings` — or, where the carrier supports outgoing parcels, `Incoming
parcels` / `Outgoing parcels` / `Settings` (see the next section).
`Parcels` is one editable multi-code list; `Settings` is
a flat form — some carriers use one sectioned form
(`data_entry_flow.section`) instead; both are generator variants, not carrier
decisions. Changes apply without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default, and the `--auth byo-key` build) apply
  changes live: an update listener calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately (this is also the resume
  path after polling has fully suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Auth models

Three `--auth` builds, one axis: **what the config flow asks for and
validates**, not how tracking works. `none` and `byo-key` both key tracking on
codes the user types in (`Parcels`/`Settings` options, `track_parcel` /
`untrack_parcel` services, the account-less coordinator and its full-stop /
delivered-skip behaviour below) — `byo-key` only adds a required key field
validated against the carrier's **official** API at setup, `CONF_API_KEY` in
`entry.data`, and a reauth flow for when the key is rotated or revoked outside
Home Assistant (`AmazonAuthError` from any per-parcel fetch raises
`ConfigEntryAuthFailed` for the whole poll — one credential covers every
tracked code, so a rejected key is never treated as one parcel's problem).
`credentials` is the only one that changes the *tracking* model too (an
account feed, not user-entered codes) — see the split above.

Reach for `byo-key` only when carrier-research has already established that
the carrier's *unauthenticated/consumer* surface is unusable (bot-walled,
requires a session a script can't hold) and that the *official* developer key
is reachable by a private individual — `key_access: consumer` in the research
doc's front matter, not `business`. A `business`-gated key is a wall, not a
BYO key, whatever the portal's own copy claims (see
`carrier-research/CLAUDE.md`'s "Standing rulings").

## Incoming and outgoing parcels

**Add outgoing support whenever the carrier lets a consumer send a parcel** —
a C2C shipment, a locker drop-off, a marketplace or returns label. It is not
an optional extra to bolt on later: without it a parcel the user sent counts
towards `incoming_active` and sits on their dashboard next to the ones they
are waiting for. A carrier that genuinely has no consumer-sending surface is
exempt — say so in *Carrier-specific notes* so the gap reads as a decision.

Where the direction comes from has exactly two answers, and which one applies
follows from the carrier, not from taste:

- **Account-based** — the account feed distinguishes them, so derive it and
  never ask the user. A shipment matching neither side logs a one-shot
  warning and defaults to incoming rather than disappearing from every list.
  References: `ha-ppl-cz` (one call, split on a field), `ha-dhl-nl` (a
  separate "sent" endpoint).
- **Account-less** — the payload cannot reveal it (the user's own parcel and
  a stranger's look alike, and there is no account identity to compare a
  party against), so the **user declares it per parcel**: two menu entries in
  the options flow, each the same multi-code list, plus a `direction` field on
  `track_parcel`. Store it as `CONF_DIRECTION` on the `CONF_PARCELS` dicts,
  defaulting to incoming, so entries written before the option existed need no
  migration. Re-filing a code under the other direction moves it instead of
  erroring — that is the correction path. Reference: `ha-packeta`.
  **Never infer direction from free-text event wording**: a handover sentence
  that happens to name the drop-off point is not a structured field, says
  nothing before handover, and silently ties the split to one locale.

Above that split the shape is identical either way, and is suite-wide:
`coordinator.outgoing` / `coordinator.delivered_outgoing` alongside `data` /
`delivered`; `outgoing_parcels` + `outgoing_delivered_parcels` summary
sensors (their unique_ids belong in `non_parcel_unique_ids`); per-parcel
sensors spawned for **both** directions; the
`<domain>_outgoing_parcel_status_changed` / `_outgoing_parcel_delivered`
event pair, with **no** `registered` and no delivery-time event for outgoing;
`awaiting_pickup`, `next_delivery` and the calendar staying incoming-only;
and both new lists in `diagnostics.py`. The aggregator needs no change — it
buckets on the sensor suffix and the event prefix. Also set
`directions: incoming+outgoing` for the carrier in the docs site's
`data/carriers.yml`.

## Tracking-code validation

In code-based carriers (account-based sources have no code entry),
`valid_tracking_code` in `config_flow.py` accepts every non-empty code — no
format regex. This is a suite-wide convention, not a per-carrier TODO: real
tracking-number formats vary too much across carriers, and are often not
fully confirmed even for this one, to gate on a guessed shape. A too-strict
regex risks rejecting a genuinely valid code; an actually-bad code just comes
back "not found" on the next poll, which is a far cheaper failure mode. Do
not add one back in, even once the format is confirmed.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `coordinator.py`'s `_hottest_tier_minutes` /
`_next_update_interval` recompute `update_interval` at the end of every
refresh. The reference carrier in `ha-carrier-template` (its `coordinator.py`)
is the canonical implementation every carrier mirrors; the design rationale (quiet window, tiers, stagger,
backoff, delivered-skip) is spelled out below.

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot. Account-based carriers
  never fully stop even with nothing hot or in transit: the mid-tier poll is
  also how a new shipment gets discovered.
- **Full stop (account-less carriers only):** `update_interval = None` when
  nothing is tracked or every tracked parcel is delivered. Resumes the moment
  a parcel is added back, via the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — the carrier's own `Retry-After` header if present, otherwise
  an exponential backoff tracked per-coordinator. `api.py`'s
  `…ApiError.status_code` / `.retry_after` carry this from the HTTP layer.
- **Delivered codes are skipped from the fetch (account-less carriers only):**
  once a tracking code's payload comes back `delivered`, `coordinator.py`
  excludes it from the next cycle's fetch — its payload can never change
  again. `self._delivered_codes` (keyed on the tracking code, not the barcode)
  is rebuilt from each cycle's results and intersected with the tracked set on
  untrack. The code stays in the options list, keeps its sensor and its
  cached payload, and still shows under the retention window — it just costs
  no more requests. `coordinator.delivered_codes` surfaces the count in
  diagnostics. Account-based carriers have nothing to skip here — one account
  call already returns everything, so they either have no `delivered_codes` or
  it is always empty.

A carrier that genuinely throttles or soft-bans traffic harder than the 429
backoff handles is a documented, local divergence from this in that one
repo's own `CLAUDE.md` — not a generator flag.

## Module layout

| File | Carrier-specific? |
|---|---|
| `account/` (the one source: `auth.py` sign-in, `client.py` page fan-out, `pages.py` HTML parsing, `parcels.py` status map + `normalize_parcel`, `coordinator.py`, `errors.py`) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `config_flow.py` | partly (code validation; key/credential validation on `--auth byo-key`/`credentials`) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `device.py` (shared device-info helper) | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.amazon_orders
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
