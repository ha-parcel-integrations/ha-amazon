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
- **The pickup-point summaries are a pair.** A carrier that can deliver to a
  pickup point or locker exposes both `en_route_to_pickup_point` and
  `awaiting_pickup`, even before a real parcel has shown the status or flag
  (they read 0 until then; record it in `PENDING_*`). A carrier without pickup
  points exposes neither and keeps `pickup` constantly False.
  See *Parcel contract* in `CONVENTIONS.md`. Say "pickup point", not
  "ServicePoint"/"parcel shop"/"locker", for the generic concept; the
  example carrier demonstrates both canonical sensors.

## Carrier-specific notes

API mechanics (the sign-in chain, the shape of each response, vocabularies,
what was tried and failed) live in the private
`carrier-research/amazon-logistics/api/` and are never copied here. This
section keeps decisions only.

**Sign-in durability, partly open.** The repo is public and released as 0.x;
the maintainer built and shipped ahead of the research gate. A continuously
polling install has held one sign-in for 4+ days across restarts without a
reauth. Still unanswered: whether a sign-in survives days with Home Assistant
off, and whether signing in on a second install with the same account ends the
first one's sign-in. Settle both before 1.0.0.

Account model reading Amazon's own signed-in surfaces: a named maintainer
exception for Amazon **only**, never a precedent for another carrier. Prefer
the structured state a response carries over anything meant for display.

- **One config entry per Amazon account per country** (`unique_id` =
  `<storefront>:<account id from the registration>`, else the device serial
  when Amazon names no account); `single_config_entry` is deliberately not
  set. Entries from 0.2.0 and earlier are keyed on the storefront alone and
  adopt the account key on their first reauth, as does an entry keyed on its
  serial (Amazon named no account): neither has an account to check against,
  so its reauth takes the new key, unless another entry already holds it
  (`already_configured`). Reauth of an account-keyed entry with another
  account aborts `wrong_account`.
- **Sign-in is a pasted link, never a password form.** Entry data holds the
  storefront, the refresh token and the device serial, all redacted in
  diagnostics. Never fall back to a password login.
- **"No order lines" says what came back** (`describe_page`): path, title, size,
  a few counts. A bot check and a new layout read the same otherwise. Only
  structure goes in, because users paste this line into public issues.
- **Encrypted order cards are read again once.** Amazon sometimes serves the
  orders page with its cards encrypted (`csd-encrypted-sensitive`) for its own
  script to decrypt; the same path with `disableCsd=no-js` renders them
  readable, so a page without order lines that carries the marker is fetched
  once more that way. `describe_page` reports `encrypted yes` when even that
  stayed encrypted.
- **`raw.stops_away` comes from the live map's callout** (`mapTracking.calloutMessage`,
  confirmed on amazon.de by #24's reporter, 2026-11-09): text only, so it is
  parsed from "N Stopps entfernt" or "N stops away" (the English wording is
  reported by a browser-based Amazon tracker, not yet by our own users). The
  callout naming the parcel as the next stop reads as 0: "Du bist der nächste
  Halt" is confirmed on amazon.de (2026-11-23), the "Sie" and English forms are
  plausible. Other wording warns once, masked. The rest of
  `mapTracking` is dropped from `page_state`: it holds the delivery address's
  coordinates. No event for a count change, no faster polling yet (opt-in only,
  if ever: every poll loads several pages on the user's account).
- **Auth failure -> reauth once, then polling pauses** (`AmazonAuthError` ->
  `ConfigEntryAuthFailed`). 429 and 503 back off through the coordinator,
  except a single shipment page refused that way (#49): that shipment keeps
  its last record and goes to the back of the fan-out queue, and the poll
  carries on. A second refused shipment page in a row is throttling, so it
  fails the poll; the orders list itself always does. A refused shipment
  counts as tried for `listing_complete`, so one page Amazon keeps refusing
  does not hold back `registered` for good. The refused path (no query) is
  logged at debug.
- **Fan-out is capped** (`MAX_TRACK_LOADS` shipments that load a page per
  cycle, stalest first; a delivered Business card loads none, so it is never
  counted and never waits;
  a delivered shipment is cached once it has a delivery date, or from the day
  after it first read as delivered without one; nothing delivered longer ago than
  `DELIVERED_LOOKBACK_DAYS` is followed). The **idle tier**
  (`IDLE_INTERVAL_MINUTES`) is a local divergence from the suite's tiers
  because every poll costs several requests.
- **Barcode** is the tracking id. A shipment without a tracking page is keyed
  on a stand-in from its order and line (flagged in `raw["barcode_source"]`),
  which changes to the tracking id if one appears later. That change is a
  rename, not a new parcel: the coordinator carries the stand-in's last state
  over (keyed on order, shipment and package, and on the order line id,
  because a line gains its shipment id only once it ships), so `registered` does not fire
  twice. After a restart the capped fan-out needs a few polls to read every
  shipment again; until `client.listing_complete`, a barcode that turns up
  counts as already known, so a busy account is not flooded with `registered`.
- **Status resolution:** milestone, else the tracking page's `shortStatus`
  (language-independent; an unmapped value warns once like a milestone), else
  newest mappable timeline event, else order-line text (`account/locales/`), else `unknown` + one warning. A line
  with no tracking page and no recognised text is `unknown`, never `registered`,
  and is kept out of the incoming list (`coordinator.unresolved`); only an
  explicitly recognised not-yet-dispatched text gives `registered`. Cancelled,
  returned and refunded lines are skipped at parse time. The vocabulary has a
  confirmed table (English, plus the Dutch seen on amazon.com.be, the French
  seen on amazon.fr, the German delivered wording a reporter confirmed on
  amazon.de, and amazon.es's `llega`/`llegará`, which are `in_transit` like
  `arriving` because they show before the parcel leaves the depot, and
  `entregado`, #48) and a separate
  plausible table (the other languages and the unseen wording): resolving through a plausible entry logs a
  one-shot confirmation request, and an entry moves to confirmed only once a real
  line shows it. Milestones and short statuses are mapped only once a
  real page has shown them (`ORDERED`, `SHIPPED`, `OUT_FOR_DELIVERY`,
  `DELIVERED`; `ORDER_PLACED`, `IN_TRANSIT`, `OUT_FOR_DELIVERY`, `DELIVERED`,
  `PICKED_UP`): do not add names no capture has shown. An unmapped milestone
  warns once even when the timeline resolves the status (maintainer decision): that warning is
  how the milestone map grows, so don't silence it.
- **An untrackable shipment is assumed delivered** (#47, maintainer decision):
  `exceptionStateIdentifier: UNTRACKABLE_STILL_EXPECTED_DELIVERED` (an amazon.de
  letter without tracking) otherwise stays `in_transit` for good. Its promise
  names a day ("Wahrscheinlich 14. Oktober zugestellt"), read forward from the
  order date into `raw["promised_on"]`; once that day is more than
  `UNTRACKABLE_GRACE` (3 days) past, the status is `delivered` ahead of the
  milestone, and `delivered_at` is that day, never empty, because the
  `days` retention keeps an empty `delivered_at` for good. A promise naming no
  readable day warns once and the shipment stays in transit.
- **`None` on purpose:** `sender`, `receiver`, `weight`, `dimensions`
  (`pickup_point` too, until a real name is seen).
- **`pickup`** is `is_pickup(None, status, event statuses)`: Amazon has no
  structured route flag, so it is true once the status or any timeline event
  maps to `AT_PICKUP_POINT`; `pickup_point` stays `None` until a real name is
  seen. Both pickup sensors (`en_route_to_pickup_point`, `awaiting_pickup`) are
  exposed and read 0 for now. Pending: no real pickup-point parcel has been
  seen, so no milestone, short status or timeline wording maps to
  `AT_PICKUP_POINT` yet; map it once a capture shows one.
- **Window:** `planned_window` (Home Assistant's zone) and `planned_for_status` in
  `account/parcels.py` keep the predicted window and empty it once delivered;
  `next_delivery` skips a parcel whose window has ended.
- **Delivery window** only when the tracking page predicts one: the times come
  from the promise text, the day from the countdown to the window's end, and the
  two must agree within an hour or the window is dropped with a one-shot
  warning. Never on a delivered parcel. The countdown is seen on amazon.fr; amazon.de's
  promise reads "Zustellung heute 17h - 19h" (#24), so the `17h`/`17h30` shape
  is read too, as are bare 12-hour hours (`3 pm - 6 pm`, #39). amazon.de
  sends no countdown (#24, #37), in English either (#39): a window without
  one takes its day from the message's own `Ankunft`/`Zustellung` +
  `heute`/`morgen`, or `[Now] arriving` + `today`/`tomorrow`, in the page's zone; one naming no such day warns once
  instead of being dropped silently.
  Fallback: an order line naming only a day (`Arriving tomorrow`, `today`,
  `Arriving Oct 27`, `Oct 27-29`, `Oct 30-Nov 2`, day first as amazon.de
  writes in English, `Arriving 20 November`, `3-5 November`,
  `20 November - 18 December`, and `Arriving Wednesday` for the next such
  weekday, today included (#40); amazon.de's
  `Zustellung: <Wochentag>` (#27, colon optional) for the next such weekday,
  today included, `Ankunft`/`Zustellung` + `heute`/`morgen`, and
  `Zustellung: 20. Oktober` or `Zustellung: 20. Oktober - 21. Oktober`;
  amazon.es's `Llega hoy`/`Llega mañana` and `Llegará el 27 de noviembre`,
  a weekday ahead of the day allowed (#48: the reporter described its shape,
  not a verbatim line);
  matched whole so a timed line never qualifies) gives a
  whole-day window, 00:00 to 23:59:59 in the page's zone, else the HA zone
  `read_at` carries; the calendar shows it as an all-day event (#43). The timed window wins when both exist. A timed order
  line (`Arriving today 6 pm – 10 pm`, #40) gives its times only when the
  tracking page carries no promise of its own, so a window the countdown
  check dropped never returns through the order line; otherwise, or when
  the times are unreadable, it gives the day its `window_day` word names.
  An order line starting with a locale's `arrival_leads` (English
  `arriving`, Spanish `llega`, which also catches `llegará`) that gives no
  day warns once, masked. Only the wording a
  real line has shown: `am <date>` is deliberately not read, and no other
  language is. The two
  weekday fields are separate on purpose — `upcoming_weekdays` names days
  ahead, `past_weekdays` the past weekday a delivered line can carry. No structured date exists to prefer: an in-flight tracking page's
  `promise.promiseMessage` carries the same text as the order line
  ("Arriving tomorrow", seen 2026-10-25), `secondaryPromiseIdentifier` is `NONE`.
- **Delivered day** from the order line's date, else from a relative word
  (today, yesterday, a French weekday within the past week). A day with no
  delivered event is midnight in Home Assistant's zone, never UTC. A delivered text
  longer than the bare status word that gives no day warns once, so the
  wording can be added.
- **An order line is split on its own "order placed" anchor** (English,
  French and German), else by looking for the first comma-separated segment
  that opens with a known status wording. amazon.de's
  "Bestellung aufgegeben [am] 2. November 2026, Zustellung: Dienstag" (#27)
  needed the German anchor *and* a plain `zustellung` prefix: without both the
  status segment came out empty, so the parcel resolved to `unknown` and was
  hidden from the incoming list altogether. The anchor rests on a reported
  label, not our own capture — treat a storefront whose label shape is
  unconfirmed the same way, since a missed anchor silently hides a parcel
  rather than logging anything useful.
- **Dates are read day-first, then month-first.** The European storefronts
  write "3 October" / "Order placed 2 October 2026"; amazon.com writes
  "November 9" / "Order placed November 8, 2026" (#32). Day-first is tried
  first on purpose: a text that reads both ways is far likelier to be the
  European shape. The same two shapes serve the order line's placed and
  delivered dates and the tracking timeline's date headers.
- **The delivery photo is an `image` entity, and its link never reaches `raw`**
  (maintainer decision, 2026-11-03, after the hacs/default review). The URL is a
  **presigned S3 link** (confirmed by the reporter, 2026-10-11): it opens without
  signing in and shows the user's front door, while anything in `raw` reaches
  attributes and recorded event payloads. So the client keeps the link to itself
  (`delivery_photo_url()`), not in the record, and Home Assistant serves the bytes
  behind its own authentication. `raw["delivery_photo_entity_id"]` is what an
  automation gets instead. Only Amazon's own fleet renders the card. Parse it from
  the lazy `data-src`, never `src` (a `data:` placeholder), and keep the
  `<noscript>` twin as the fallback anchor — it has no class, so it survives a
  class rename. Don't hardcode the bucket host: it is regional and only one
  storefront's has been seen. **Two tripwires:** photos are synced *before*
  `_fire_change_events`, through the platform's awaitable add, because an
  automation on the delivered event loads the image at once; don't move that to a
  coordinator listener. And the link dies about 72 h after we read the page, while
  a delivered shipment enters `client._final` and is never re-read, so the bytes
  are downloaded straight away and kept in memory (only for parcels the delivered
  list shows). A restart re-reads the page and so gets a fresh link. A card that
  appears only after the parcel went final is not seen until a restart: no capture
  has shown that lag, so a shipment isn't held back for it.
- **The timeline is in the account's language.** A locale's `events` holds only wording
  a real account has shown (English, plus Dutch, German and French from the
  Belgian, German and French storefronts) — unlike
  the order-line status, it has no plausible tier, because a wrong guess here silently
  mis-states history. Messages are matched after stripping trailing `.`/`!`: Amazon
  ends some and not others. Status resolution is unaffected either way (the
  milestone and `shortStatus` are language-independent and answer first), so an
  unmapped language costs history quality and log noise, not correctness.
- **`raw` carries the whole shipment record**; privacy is the diagnostics
  redaction's job only. It has exactly one documented exception to the suite's
  "`raw` is never trimmed" rule: `_STATE_DROP` in `account/pages.py` drops the
  map-provider key Amazon embeds beside the delivery state. It is a
  third-party credential rather than anything about the parcel, so it is
  dropped where the record is read instead of being carried into every
  parcel's attributes. Add nothing else to that set.
- **An order line may link straight to its tracking page** (amazon.fr does
  for in-flight lines): it is then read as the tracking page, one request
  instead of two. Only the tracking page's own path counts, never a sub-path
  under it: a delivered line's page links a delivery-feedback survey there
  ahead of the real tracking link, so the tracking-link pattern stays narrow.
- **Amazon Business** (seen on amazon.fr): the orders page is an empty shell
  and the list comes from a separate call whose parameters and anti-forgery
  token the shell carries; the client switches to it only when the shell
  says Business and the page has no consumer tile, so other accounts are
  untouched. It asks for **every** order of the last three months (any
  buyer, any payer, any organisation; maintainer decision, so parcels the
  organisation pays for are not missed), never the page's own fallback,
  which is the narrowest option. It reads page after page, as the page does
  on scroll, until one comes back with no card, capped at
  `MAX_BUSINESS_PAGES` with a one-shot warning: pages vary in size, and the
  list is sorted by order date, so a parcel still on its way can sit past
  the first page. A rejected token gets one fresh shell,
  then reads as an auth error. An empty list warns once: a malformed call
  returns the same empty list as an account without orders. So do a card
  with no order and fewer readable cards than the list says it rendered,
  since either silently drops a parcel. In-flight cards
  link to the tracking page; a delivered card links to an order page with no
  tracking and is never followed, so the client remembers each line's
  shipment and tracking link (keyed on order and title) to keep the same
  parcel when its card turns delivered. That memory is persisted per entry
  (a `Store`, removed with the entry) so a restart does not change the
  barcode, and an empty list never prunes it. A delivered card it never saw in
  flight is keyed on its order and a digest of its title, never on its place
  in the list.
- **No outgoing parcels:** the account only lists what the user ordered.
- **Wording lives in `account/locales/`, one file per language** (`en.py`,
  `nl.py`, `de.py`, …), as pure data on the `Locale` shape in `base.py`, so a
  native speaker can check or extend one file. `locales/__init__.py` merges
  them, and **every language is always tried**: the language follows the
  account's setting, not the storefront (amazon.de can be set to English,
  amazon.com.be serves Dutch and French). Never pick a locale per entry. Only
  grammar the generic parser can't express (the arrival and window-day shapes)
  is a regular expression in the language file; everything else is a word
  list. `tests/account/test_locales.py` fails when two languages read the same
  word differently.
- **Not built:** browser automation, IMAP parsing, order-level sensors,
  alexapy as a requirement, and a separate `ha-amazon-logistics` repo (Amazon
  Logistics has no tracking outside the signed-in account, so it would be an
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
  `track_parcel` there). `DPDDE` (DPD Germany, #41) is named `DPD` but
  deliberately not in the table: `dpd.track_parcel` only adds to a myDPD app
  account and refuses DPD Germany, so a German user would get a Repair issue
  and refused hand-offs. Add it once ha-dpd takes a DPD Germany number. On amazon.ca, a tracking page names the courier
  plainly as `Intelcom` rather than by an Amazon code; `account/pages.py`'s
  `_CARRIER_ALIASES` folds it into `DRAGONFLY` before it reaches this table,
  since Intelcom is Dragonfly Shipping's Canadian brand and `ha-dragonfly`'s
  single hub already resolves to whichever country backend its one
  config entry was set up for. On amazon.fr only, a tracking page naming no courier
  (no header, or the generic one) whose id has the Colissimo shape is shown
  as La Poste (`carrier_code: "LA_POSTE"`, not an Amazon code;
  `raw["carrier_guessed"]`, one-shot warning). Never guess on another
  storefront or over a header we could not read, and **never hand off a
  guess** (maintainer decision): a wrong one would hide the parcel here and
  strand it at the carrier. Lift that only once a real hand-off confirms it.
- **Hand off only when `hass.services.has_service(domain, "track_parcel")`**,
  and only after `EVENT_HOMEASSISTANT_STARTED`: during startup the carrier may
  not have registered its service yet, which would read as "not installed".
- **Never handed off:** `carrier: "Amazon"` (Amazon Logistics; amazon.es's
  header `Enviado con Amazon` reads as it, #48) and any parcel
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
- **`contents` goes with the hand-off** (the item titles, or the user's own
  `update_contents` value), left out when empty: the parcel is hidden here
  from then on. Every carrier in `HAND_OFF_CARRIERS` must take `contents` on
  `track_parcel`, or the call is refused and the parcel stays here. A later
  `update_contents` here is not pushed on: the carrier is the authority.
- **Handed-off codes are remembered** per entry (code -> domain + hand-off
  time), so the service is not called every poll and the parcel stays hidden
  here. A code is forgotten only when it is missing from the order list
  **and** was handed off more than `FORGET_AFTER` (30 days) ago.
- **Carrier in the table but not set up:** one Repair issue **per carrier
  domain** (not per parcel, not per entry), linking to that carrier's page on
  the docs site; not fixable, ignorable. It is deleted when the carrier's
  service appears, or with the last Amazon entry, never when the parcels
  needing it go away (that would re-nag a user who ignored it).
- **Covered by an account-based sibling:** `HAND_OFF_COVERED_BY` (`dhl` ->
  `dhl_nl`) means a set-up `dhl_nl` entry stops the `dhl` Repair issue (and
  clears a raised one). The parcel is still shown here, not handed off; the
  aggregator de-duplicates.
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

One sectioned options form (delivered retention, history, hand-off). The flow is an
`OptionsFlowWithReload` and registers **no** update listener (the two may not be
combined). Don't call `async_schedule_reload` from the submit instead: that
reload starts before the new options are saved, so anything built from
`entry.options` at setup (the hand-off switch) keeps the old value.

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
| `account/` (the one source: `auth.py` sign-in, `client.py` request fan-out, `pages.py` response reading, `locales/` Amazon's wording per language, `parcels.py` status map + `normalize_parcel`, `coordinator.py`, `errors.py`) | **yes** |
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
