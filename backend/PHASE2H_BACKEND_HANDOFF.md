# SOULTIE — Phase 2H Backend Handoff

## Implemented backend
- Canonical mutual matching and compatibility
- Match Experience v1 and icebreakers
- Authenticated match.created realtime delivery
- Per-recipient private Socket.IO rooms
- Access-token validation and session revalidation
- Refresh-token rotation, logout and replay protection

## Mobile integration contract
1. Authenticate using existing SOULTIE login endpoints.
2. Store the latest refresh token in platform-secure storage.
3. On app launch, attempt session renewal before displaying login.
4. On token expiry, refresh once and replace both stored tokens atomically.
5. Send Socket.IO auth with `access_token`, not `token`.
6. Never treat an app restart or access-token expiry as a logout.
7. Revoke the refresh session on explicit logout.
8. Handle revocation, disabled accounts and compromised credentials safely.

## Persistent-login limitation
Rolling refresh currently supports continued login while the session
is renewed within its configured lifetime. The existing inactivity
expiry policy must be explicitly resolved before claiming indefinite
login after prolonged inactivity.

## Acceptance
The existing Phase 2H selected integration suite is the regression
baseline. Review the consolidated report for the latest executed count.

## Release control
Do not commit or push until complete Phase 2H acceptance is approved.
