# SOULTIE FCM Backend API Handover

Firebase project: `soultie-a0487`

## Authentication

Use the existing SOULTIE Bearer access token for device API calls.

## Register or update a device

`POST /api/v1/push-devices`

```json
{
  "device_id": "stable-app-installation-id",
  "platform": "android",
  "push_provider": "fcm",
  "push_token": "REAL_FCM_TOKEN",
  "device_name": "User's Phone"
}
```

Supported mobile platforms: `android`, `ios`.

The existing backend uses the authenticated user as the owner.
The endpoint also updates tokens for an existing user/device pair.
Registration requires a valid access token.

## List registered devices

`GET /api/v1/push-devices`

## Revoke a device

`DELETE /api/v1/push-devices/{device_id}`

Call device revocation before explicit logout while the access token
is valid. Then call the existing authentication logout endpoint.

## Token lifecycle

Register on login/session restoration and on FCM token refresh.
Use an installation-specific device ID, not a hardware identifier.
Do not include private Firebase service-account credentials
in the mobile application.

## Current limitations

Automatic server-side push-device revocation from logout is
not yet verified or implemented.
Production Celery push delivery remains disabled.
iOS APNs setup and device presentation are handled separately.
FCM acceptance is not proof of device display.

## Controlled acceptance testing

Set `FCM_TEST_DEVICE_TOKEN` in the backend terminal environment.

Validation-only:
`python tools/fcm_acceptance.py`

Explicit real send:
`python tools/fcm_acceptance.py --send --confirm-send`

Never place device tokens in source code or Git.
