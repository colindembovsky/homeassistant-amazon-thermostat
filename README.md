# Amazon Thermostat for Home Assistant

Native Home Assistant custom integration for Alexa-connected thermostats.

This integration is based on the behavior documented from
[`homebridge-alexa-smarthome`](https://github.com/joeyhage/homebridge-alexa-smarthome).
It talks to Amazon's private Alexa Smart Home GraphQL API to discover thermostats,
read current temperature/humidity/mode/setpoints, and send set-temperature commands.

## Current status

Setup uses a Python port of the Homebridge `alexa-cookie2` login flow to open a
guided Amazon login through Home Assistant. This is the only supported
authentication method.

## Supported features

- Alexa thermostat discovery
- Current temperature
- Target temperature
- Heat/cool target range for Alexa `AUTO` mode
- HVAC modes: heat, cool, heat/cool, off
- Optional indoor humidity when Alexa exposes the `Indoor humidity` range feature

## Known limitations

- Amazon does not provide an official public API for this use case. The private API
  can break without notice.
- Authentication uses an unofficial Alexa web login/device-registration flow;
  Amazon can change this flow without notice.
- Amazon's SMS/e-mail verification flow may fail in this unofficial proxy. Use
  app-based MFA/TOTP for the best chance of success, and open the login URL from
  a browser/device without the Alexa app installed.
- The integration intentionally does not expose `hvac_action` yet because the
  upstream Homebridge implementation does not have a reliable source for active
  heating/cooling state.
- ECO is not exposed as a settable preset. Many Alexa thermostats reject
  `setThermostatMode ECO` with a `BAD_REQUEST` error, and the upstream Homebridge
  plugin never writes ECO either. If the thermostat is placed in ECO elsewhere
  (e.g. the Alexa app), it is reported as the heat/cool mode.
- Polling is intentionally conservative to reduce rate-limiting risk.

## Installation

### Install with HACS

The easiest way to install this integration is as a HACS custom repository:

1. In Home Assistant, open **HACS**.
2. Go to **HACS > Integrations**.
3. Open the three-dot menu and choose **Custom repositories**.
4. Add this repository URL, for example:

   ```text
   https://github.com/colindembovsky/homeassistant-amazon-thermostat
   ```

5. Set the category to **Integration**.
6. Click **Add**.
7. Search HACS for **Amazon Thermostat** and install it.
8. Restart Home Assistant.
9. Go to **Settings > Devices & services > Add Integration** and choose
    **Amazon Thermostat**.

During setup:

1. Choose your Amazon region and polling interval.
2. Confirm the **Home Assistant URL**. The default is
   `http://homeassistant.local:8123/`.
3. Optionally enter an **External Home Assistant URL** if you use Nabu Casa or a
   reverse proxy.
4. Confirm the **Login proxy port** (default `8124`). The guided login runs a
   small reverse proxy at the *root* of this dedicated port, mirroring how the
   Homebridge `alexa-cookie2` proxy runs on its own host/port. This is required
   so Amazon's mobile-verification (CVF) page — a single-page app that makes
   root-relative requests such as `/ap/cvf/verify` — is correctly intercepted.
   A Home Assistant sub-path proxy cannot capture those requests, which is why
   verification fails there.
5. Complete the Amazon login page that opens through Home Assistant. It opens at
   `http://homeassistant.local:8124/` (your host and chosen port). Enter your
   Amazon username, password, and MFA code on that Amazon page. For the best
   chance of success, open this from a device/browser that does **not** have the
   Alexa app installed.

> **Docker/Container note:** the login proxy binds the dedicated port (default
> `8124`) on the Home Assistant host. On Home Assistant OS/Supervised this works
> out of the box. On Home Assistant Container/Docker you must publish that port
> (for example `-p 8124:8124`) so the browser can reach it. This is the same
> limitation the Homebridge proxy has.

The integration intentionally does not autofill Amazon credentials. This avoids
blank Home Assistant form values interfering with Amazon's MFA or verification
pages.

If Amazon selects its phone/SMS verification path and says it cannot verify
your mobile number, use app-based MFA/TOTP on your Amazon account if available,
open the login from a desktop browser without the Alexa app installed, and try
setting **Home Assistant URL** to your instance's LAN IP address, for example
`http://192.168.1.50:8123/`, instead of `homeassistant.local`.

## Security warning

Alexa session data can grant broad access to your Amazon account. Do not share logs
or diagnostics unless sensitive fields have been redacted. This integration redacts
stored cookie, CSRF, password, OAuth token, and session fields from diagnostics.
