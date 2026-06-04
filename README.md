# Amazon Thermostat for Home Assistant

Native Home Assistant custom integration for Alexa-connected thermostats.

This integration is based on the behavior documented from
[`homebridge-alexa-smarthome`](https://github.com/joeyhage/homebridge-alexa-smarthome).
It talks to Amazon's private Alexa Smart Home GraphQL API to discover thermostats,
read current temperature/humidity/mode/setpoints, and send set-temperature commands.

## Current status

This is an early implementation scaffold. The primary setup path now uses a
Python port of the Homebridge `alexa-cookie2` login flow to open a guided
Amazon login through Home Assistant. The older `alexapy` flow and a manual Alexa
web cookie + CSRF setup path remain available as fallbacks.

## Supported features

- Alexa thermostat discovery
- Current temperature
- Target temperature
- Heat/cool target range for Alexa `AUTO` mode
- HVAC modes: heat, cool, heat/cool, off
- ECO as a Home Assistant preset
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
- Polling is intentionally conservative to reduce rate-limiting risk.

## Installation

### Install with HACS

The easiest way to install this integration is as a HACS custom repository:

1. Push or fork this repository to GitHub.
2. In Home Assistant, open **HACS**.
3. Go to **HACS > Integrations**.
4. Open the three-dot menu and choose **Custom repositories**.
5. Add this repository URL, for example:

   ```text
   https://github.com/colindembovsky/homeassistant-amazon-thermostat
   ```

6. Set the category to **Integration**.
7. Click **Add**.
8. Search HACS for **Amazon Thermostat** and install it.
9. Restart Home Assistant.
10. Go to **Settings > Devices & services > Add Integration** and choose
    **Amazon Thermostat**.

### Manual install fallback

If you are not using HACS, copy `custom_components/amazon_thermostat` into your
Home Assistant `custom_components` directory, restart Home Assistant, then add the
integration from **Settings > Devices & services**.

During setup:

1. Choose your Amazon region.
2. Keep **Guided Amazon login** selected. This is the Homebridge-compatible
   `alexa-cookie2` flow ported to Python.
3. Confirm the **Home Assistant URL**. The default is
   `http://homeassistant.local:8123/`.
4. Optionally enter an **External Home Assistant URL** if you use Nabu Casa or a
   reverse proxy.
5. Complete the Amazon login page that opens through Home Assistant. Enter your
   Amazon username, password, and MFA code on that Amazon page.

The integration intentionally does not autofill Amazon credentials. This avoids
blank Home Assistant form values interfering with Amazon's MFA or verification
pages.

If guided login is unavailable, try **Fallback: alexapy guided login**. If both
guided methods fail, choose **Advanced: manual cookie and CSRF** and paste a
current Alexa web cookie header plus its matching CSRF token.

If the guided login reaches an `/ap/cvf/request` URL and Amazon says it cannot
verify your mobile number, Amazon has selected its phone/SMS verification path.
That path is unreliable in unofficial Alexa proxies. Use app-based MFA/TOTP on
your Amazon account if available, open the login from a desktop browser without
the Alexa app installed, and try setting **Home Assistant URL** to your
instance's LAN IP address, for example `http://192.168.1.50:8123/`, instead of
`homeassistant.local`.

## Security warning

Alexa session data can grant broad access to your Amazon account. Do not share logs
or diagnostics unless sensitive fields have been redacted. This integration redacts
stored cookie, CSRF, password, OAuth token, and session fields from diagnostics.
