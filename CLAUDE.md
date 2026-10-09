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

API mechanics (sign-in chain, page structure, vocabularies, what was tried and
failed) live in the private `carrier-research/amazon-logistics/api/` and are
never copied here. This section keeps decisions only.

**Sign-in durability, partly open.** The repo is public and released as 0.x;
the maintainer built and shipped ahead of the research gate. A continuously
polling install has held one sign-in for 4+ days across restarts without a
reauth. Still unanswered: whether a sign-in survives days with Home Assistant
off, and whether signing in on a second install with the same account ends the
first one's sign-in. Settle both before 1.0.0.

Account model with an HTML source: the carrier-research standing ruling
permits parsing signed-in pages for Amazon **only** (a named exception, not a
precedent for any other carrier). Prefer embedded JSON state over visible text.

- **One config entry per Amazon account per country** (`unique_id` =
  `<storefront>:<account id from the registration>`, else the device serial
  when Amazon names no account); `single_config_entry` is deliberately not
  set. Entries from 0.2.0 and earlier are keyed on the storefront alone and
  adopt the account key on their first reauth; reauth with another account
  aborts `wrong_account`.
- **Sign-in is a pasted link, never a password form.** Entry data holds the
  storefront, the refresh token and the device serial, all redacted in
  diagnostics. Never fall back to a password login.
- **Auth failure -> reauth once, then polling pauses** (`AmazonAuthError` ->
  `ConfigEntryAuthFailed`). 429 and 503 back off through the coordinator.
- **Fan-out is capped** (`MAX_TRACK_LOADS` shipments per cycle, stalest first;
  a delivered shipment is cached once it has a delivery date, or from the day
  after it first read as delivered without one; nothing delivered longer ago than
  `DELIVERED_LOOKBACK_DAYS` is followed). The **idle tier**
  (`IDLE_INTERVAL_MINUTES`) is a local divergence from the suite's tiers
  because every poll costs several requests.
- **Barcode** is the tracking id. A shipment without a tracking page is keyed
  on a stand-in from its order and line (flagged in `raw["barcode_source"]`),
  which changes to the tracking id if one appears later.
- **Status resolution:** milestone, else the tracking page's `shortStatus`
  (language-independent; only `DELIVERED` seen, an unmapped value warns once
  like a milestone), else newest mappable timeline event, else
  order-line text (`account/vocabulary.py`), else `unknown` + one warning. A line
  with no tracking page and no recognised text is `unknown`, never `registered`,
  and is kept out of the incoming list (`coordinator.unresolved`); only an
  explicitly recognised not-yet-dispatched text gives `registered`. Cancelled,
  returned and refunded lines are skipped at parse time. The vocabulary has a
  confirmed table (English, plus the Dutch seen on amazon.com.be) and a separate
  plausible table (the other languages and the unseen wording): resolving through a plausible entry logs a
  one-shot confirmation request, and an entry moves to confirmed only once a real
  line shows it. Only the `DELIVERED` milestone has been observed: do not add
  milestone names no capture has shown. An unmapped milestone warns once even
  when the timeline resolves the status (maintainer decision): that warning is
  how the milestone map grows, so don't silence it.
- **`None` on purpose:** `sender`, `receiver`, `weight`, `dimensions`,
  `pickup_point`. `delivery_window` is in `PENDING_CAPABILITIES` until a real
  in-flight parcel shows a parseable date.
- **`raw` carries the whole shipment record**; privacy is the diagnostics
  redaction's job only. The one thing dropped at parse time is a third-party
  key that is not parcel data.
- **No outgoing parcels:** the signed-in pages only list what the user ordered.
- **Not built:** browser automation, IMAP parsing, order-level sensors,
  alexapy as a requirement, and a separate `ha-amazon-logistics` repo (Amazon
  Logistics has no tracking outside the signed-in pages, so it would be an
  empty shell fed by this integration; its parcels stay here as
  `carrier: "Amazon"`).

## Hand-off to carrier integrations

This integration is a **shop**, not a carrier: it lists what was ordered and
hands each parcel to the suite integration of the courier that delivers it,
which exposes more (delivery window, pickup point, full history).

- **One option, on by default:** "hand off parcels to carrier integrations",
  a section in the options form and the last step of the config flow (shown
  pre-ticked, with one line on why). Turning it off forgets what this entry
  handed off and shows those parcels here again; it untracks nothing.
- **Mapping is a fixed table** from Amazon `carrier_code` to HA domain
  (`DHL_CONNECT -> dhl`, `COLIS_PRIVE_BELU -> colis_prive`,
  `DRAGONFLY -> dragonfly`). DHL goes to `dhl`, never `dhl_nl` (no
  `track_parcel` there).
- **Hand off only when `hass.services.has_service(domain, "track_parcel")`**,
  and only after `EVENT_HOMEASSISTANT_STARTED`: during startup the carrier may
  not have registered its service yet, which would read as "not installed".
- **Never handed off:** `carrier: "Amazon"` (Amazon Logistics) and any parcel
  without a real tracking id (`raw["barcode_source"]` stand-in).
- **The shop always falls back to showing the parcel itself.** A parcel only
  disappears from this integration once the carrier accepted it. Nothing is
  ever lost.
- **Never untracked at the carrier** (maintainer decision): a parcel is only
  handed off once it has a real tracking id, so it physically exists at the
  carrier, which is from then on the authority on it (returns included) and
  cleans it up through its own retention. Inferring "cancelled" from a code
  leaving the order list is wrong here: after a restart the capped fan-out
  lists only part of the shipments, which would untrack and re-hand parcels.
- **Handed-off codes are remembered** per entry (code -> domain + hand-off
  time), so the service is not called every poll and the parcel stays hidden
  here. A code is forgotten only when it is missing from the order list
  **and** was handed off more than `FORGET_AFTER` (30 days) ago.
- **Carrier in the table but not set up:** one Repair issue **per carrier
  domain** (not per parcel, not per entry), linking to that carrier's page on
  the docs site; not fixable, ignorable. It is deleted when the carrier's
  service appears, never when the parcels needing it go away (that would
  re-nag a user who ignored it).
- **Carrier in the suite without `track_parcel`** (account-based: PostNL, DPD,
  …): no hand-off, no Repair issue. The carrier account already shows the
  parcel; de-duplication is the aggregator's job (carrier record wins over the
  shop record).
- **Carrier not in the suite:** shown here, no Repair issue. Unknown
  `carrier_code` keeps its existing one-shot warning.
- **Service call fails** (`ServiceValidationError`, e.g. invalid code or more
  than one hub): one WARNING per code saying to add it by hand, fall back to
  showing it here, retry only after a reload.
- **Implementation notes.** Delivered parcels are never handed off. Hand-off failures
  catch `HomeAssistantError`, not just `ServiceValidationError`. The Repair
  issue is neither raised nor cleared while the option is off. The gate is
  `hass.state is CoreState.running`, not `hass.is_running` (true while still
  starting). The options and config-flow strings carry the option; the docs-site
  slug lives in `HAND_OFF_CARRIERS` and reaches the issue as a placeholder.
- **Attribution:** `account/auth.py` is adapted from alexapy (Apache-2.0). Keep
  its docstring, `NOTICE`, `LICENSE-Apache-2.0` and the README Credits in step,
  and credit alexapy in any commit that touches that code. The docs-site
  carrier entry still owes the same credit. Watch alexapy releases for
  protocol fixes.

## Options and reloads

One sectioned options form (delivered retention, history, hand-off). The submit calls
`async_schedule_reload` and registers **no** update listener; combining a
listener with a reload-on-update flow is deprecated, an error in HA 2026.12+.

## Dynamic polling

There is no user-facing polling interval, by suite convention.
`coordinator.py`'s `_hottest_tier_minutes` / `_next_update_interval` recompute
`update_interval` after every refresh: quiet window 00:00-06:00 with two
anchors, *hot* (15 min) for an out-for-delivery parcel, *mid* (45 min) for
anything else in flight, a per-install stagger, and `UpdateFailed` with
`retry_after` on 429/503. Local divergence: an **idle** tier when nothing is in
flight. The account never fully stops polling, since polling is also how a new
shipment is discovered.

## Module layout

| File | Carrier-specific? |
|---|---|
| `account/` (the one source: `auth.py` sign-in, `client.py` page fan-out, `pages.py` HTML parsing, `parcels.py` status map + `normalize_parcel`, `coordinator.py`, `errors.py`) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `config_flow.py` | partly (country picker and sign-in link) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `handoff.py` (hand-off to carrier integrations: service calls, remembered codes in a per-entry `Store`, Repair issues; the carrier table is `HAND_OFF_CARRIERS` in `const.py`, the coordinator runs it in `_async_publish` and re-runs it on `EVENT_HOMEASSISTANT_STARTED`) | partly (the table) |
| `device.py` (shared device-info helper) | no |
| `diagnostics.py` | partly (`TO_REDACT`) |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is not caught around the update (the coordinator wraps it). Entities: `has_entity_name` + `translation_key`,
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
