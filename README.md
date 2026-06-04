# Amazon Thermostat for Home Assistant

Native Home Assistant custom integration for Alexa-connected thermostats.

This integration is based on the behavior documented from
[`homebridge-alexa-smarthome`](https://github.com/joeyhage/homebridge-alexa-smarthome).
It talks to Amazon's private Alexa Smart Home GraphQL API to discover thermostats,
read current temperature/humidity/mode/setpoints, and send set-temperature commands.

## Current status

This is an early implementation scaffold. The primary setup path now uses
`alexapy` to open a guided Amazon login flow through Home Assistant. An advanced
manual Alexa web cookie + CSRF setup path remains available as a fallback.

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
- Authentication uses the unofficial Alexa web login flow through `alexapy`; Amazon
  can change this flow without notice.
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
2. Keep **Guided Amazon login** selected.
3. Confirm the **Home Assistant URL**. The default is
   `http://homeassistant.local:8123/`.
4. Optionally enter an **External Home Assistant URL** if you use Nabu Casa or a
   reverse proxy.
5. Complete the Amazon login page that opens through Home Assistant. Enter your
   Amazon username, password, and MFA code on that Amazon page.

If guided login is unavailable, choose **Advanced: manual cookie and CSRF** and
paste a current Alexa web cookie header plus its matching CSRF token.

## Security warning

Alexa session data can grant broad access to your Amazon account. Do not share logs
or diagnostics unless sensitive fields have been redacted. This integration redacts
stored cookie, CSRF, password, OAuth token, and session fields from diagnostics.
