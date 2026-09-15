# Security

## What is on the device

A Sticky holds exactly one credential: its **device token** (`tind_…`), issued
once by `POST /api/devices` and stored in NVS (namespace `tiny`, key `cfg`,
together with device id, API base, name and WiFi networks). It authenticates the
device to tiny.technology only; it cannot act as the owner's account. NVS is not
encrypted in the default build, so anyone with USB access can read the token —
treat a lost or resold board as a leaked token and revoke it in the fleet page.

The firmware talks HTTPS with the IDF certificate bundle, accepts commands only
from the relay it is enrolled with, and refuses verbs that would echo secrets
(`config` replies with counts, never keys). The softAP provisioning portal is
open by design; it exists until the board is enrolled, and reopens only as a
rescue path after three consecutive `401`s (the token is kept, never wiped, so
a revoked device can be re-issued without a cable). OTA images are sha256-pinned
to an allowlisted host and boot as a rollback-armed trial.

## Reporting

Email the maintainer listed on the GitHub profile of `cagataycali`, or open a
GitHub security advisory on this repository. Please do not file public issues
for anything involving a token, the provisioning portal or OTA. You will get an
acknowledgement within a week.
