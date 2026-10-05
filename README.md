# Amazon Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-amazon.svg)](https://github.com/ha-parcel-integrations/ha-amazon/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-amazon/total.svg)](https://github.com/ha-parcel-integrations/ha-amazon/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Amazon](https://www.amazon.com) orders. Sign in to your own Amazon account once, and every shipment of your recent orders is imported automatically — no tracking codes to enter. Add the integration once per Amazon country you shop on.

> [!WARNING]
> **Pre-1.0.** This integration reads the pages you see when you are signed in to Amazon, not an official tracking service. Amazon can change those pages or its sign-in at any time, in which case updates fail until the integration is updated, and you may be asked to sign in again. Only delivered parcels have been confirmed so far; the statuses for parcels still on their way are mapped from the tracking timeline, and an unrecognised status shows as `unknown` and logs a warning with a link to report it.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Credits](#credits)
- [Contributing](#contributing)
- [License](#license)

## Features

- Auto-imports every shipment of your recent orders — no per-parcel setup
- One entry per Amazon country, each with its own sign-in
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), Amazon's own status text and a link to the tracking page
- The delivery carrier Amazon names (for example Dragonfly or DHL) on every parcel
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- An Amazon account for the country you want to follow. You sign in on Amazon's own page in your browser, so your password never goes through Home Assistant.
- A browser on any device to complete that sign-in once (including any verification Amazon asks for).

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-amazon` as an **Integration**.
3. Install **Amazon** and restart Home Assistant.

### Manual

Copy `custom_components/amazon_orders` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Amazon**.

1. Pick the Amazon country you shop on.
2. Open the sign-in link shown in your own browser. It is Amazon's sign-in for the Alexa app. That is expected: Home Assistant signs in as an Alexa device, so it gets a lasting sign-in without ever seeing your password.
3. Sign in and complete any verification Amazon asks for.
4. You end up on a page whose address starts with `https://www.amazon.com/ap/maplanding`, even if you shop on another country's Amazon. It may look blank or like an error, but that page is the goal. Copy its full address from the address bar and paste it back into Home Assistant straight away.

A device named "*your name*'s Home Assistant Parcels" then appears in your Amazon account under *Manage Your Content and Devices*. That is this integration. Removing it there signs Home Assistant out, and Home Assistant then asks you to sign in again.

Add the integration again to follow another country. If Amazon stops accepting the stored sign-in, Home Assistant asks you to repeat these steps.

Only shipments of recent orders are followed: those still on their way, and those delivered in the last week. To keep the load on Amazon low, at most ten shipments are read per update, so a very large number of open orders catches up over several updates.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. Shipments delivered more than a week ago are not read from Amazon. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Idle (every 2 hours)** — with nothing on its way, since every update signs in
  again and reads your orders page. It never fully stops: that's also how a new
  shipment on your account gets discovered.
- A small, fixed per-hub offset is added on top, so not every Amazon
  hub out there polls at exactly the same second.

Amazon's pages give no delivery window, so a parcel that is out for delivery is
always treated as "hot" and polled every 15 minutes until it is delivered.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Amazon → ⋮ → Delete**. Your stored sign-in is removed with the entry.

## Sensors

| Entity | Description |
|---|---|
| `sensor.amazon_orders_incoming_parcels` | Number of active shipments, full list under the `parcels` attribute |
| `sensor.amazon_orders_parcel_<code>` | One per shipment, named after its tracking id; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.amazon_orders_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.amazon_orders_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.amazon_orders_last_successful_update` | Diagnostic: when Amazon was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | The order line has no tracking yet (for example "Not Yet Dispatched") |
| `in_transit` | In an Amazon or carrier facility, or on the way between them |
| `out_for_delivery` | With the courier today |
| `delivered` | Delivered, including left in the letterbox |
| `unknown` | A status we have not mapped yet; it is logged once as a warning |

Amazon also reports other situations (a pickup point, a return, a delivery problem), but none of those have been seen on a real parcel yet, so they are not mapped. If your parcel shows `unknown`, the warning in the log names what Amazon said.

Amazon's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Amazon device):

| Event | When |
|---|---|
| `amazon_orders_parcel_registered` | A new parcel appears in the active list |
| `amazon_orders_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `amazon_orders_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), such as notifying you when a parcel is out for delivery.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.amazon_orders: debug
```

## Troubleshooting

- **Setup keeps asking me to sign in again** — Amazon ended the stored sign-in (for example after a password change or a security check). Follow the sign-in steps again; nothing else needs to change.
- **I don't end up on a `maplanding` page** — finish every step Amazon shows (verification code, passkey, puzzle) in the same browser tab. If you land on another Amazon page, open a fresh sign-in link from Home Assistant and try again, if needed in a private window.
- **The pasted address is rejected** — copy the whole address, including everything after the `?`, and paste it straight away. If it still fails, open a fresh sign-in link; each link is meant for one sign-in.
- **A parcel shows `unknown`** — Amazon said something we have not mapped yet. The log line "Unrecognised Amazon status" names it: please [open an issue](https://github.com/ha-parcel-integrations/ha-amazon/issues/new?template=unrecognised_status.yml) with that line.
- **An order shows up as a parcel named after a shipment code** — that order line has no tracking page yet, so it is keyed on Amazon's shipment code until a tracking id appears.
- **Nothing is imported** — only orders from the last few months show on Amazon's orders page, and only recent shipments are followed. Check the log for a warning about the page layout.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Amazon, Home Assistant, or any other third party referenced in this project. Please don't contact Amazon for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Amazon's terms. Data is sent only to Amazon's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration reads the signed-in pages of the Amazon website with your own account and may break when Amazon changes them.

## Credits

The Amazon sign-in and device-registration flow is adapted from [alexapy](https://gitlab.com/keatontaylor/alexapy) by Keaton Taylor and Alan Tse (Apache-2.0), the library behind [Alexa Media Player](https://github.com/alandtse/alexa_media_player). Thank you to its maintainers for working out and maintaining that flow. See [NOTICE](NOTICE) for the licence details. The suite disclaimer above applies to these projects too.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
