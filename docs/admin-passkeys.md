# Owner administration and passkeys

This release adds one owner identity, not multi-user roles. `/admin` lists named
passkeys (maximum 20), registers new ones, and revokes them. The existing access
token remains a recovery login and is required again for registration/revocation.
An authenticated owner session is also required; anonymous registration is denied.
Cloudflare Access remains in front of MHC. Never bypass its policy.

WebAuthn uses RP ID `mayhome.cuong.win` and the exact HTTPS origin
`https://mayhome.cuong.win`, not proxy headers or user supplied hosts. Visit that
hostname, not a LAN IP. Face ID, fingerprint or device PIN verification is required.
Use a synced passkey or register a second device for recovery. Private keys stay
with the authenticator; MHC stores public keys and counters only. No biometric
data is collected. Both cross-device QR authentication and synced passkeys depend
on the browser/platform. Key registration needs a real browser acceptance check.

Challenges expire in 120 seconds, are bound to the signed session, persisted for
multiple Gunicorn workers and consumed before verification. SQLite transactions
serialize counters and revocation. Revocation invalidates sessions authenticated
with that credential on their next protected request. Token sessions remain valid
until their existing expiry. A global persistent limit of 100 passkey requests per
five-minute window bounds attempts across workers (may temporarily block everyone
under attack). Token login retains its existing per-worker limiter. Keep Access
rate policies active. Admin changes require CSRF and exact Origin validation.

## Installation after reviewing the source

Prepare and activate a reviewed release using the existing upgrade procedure.
Do not rerun the initial installer. Dependencies now include webauthn 2.7.0 and
its cryptography wheels; check wheel availability on the production Python/CPU.
The feature is off by default and the token login continues to work.

Then on mayhome (root commands affect MHC only):

```sh
sudo install -d -m 700 -o mayhome-center -g mayhome-center /var/lib/mayhome-center-auth
sudo -u mayhome-center /opt/mayhome-center/.venv/bin/python -c \
  'from mayhome_center.passkeys import initialize; initialize("/var/lib/mayhome-center-auth/auth.sqlite3")'
sudo install -d -m 755 /etc/systemd/system/mayhome-center.service.d
sudo install -m 644 packaging/systemd/mayhome-center.service.d-passkeys.example \
  /etc/systemd/system/mayhome-center.service.d/passkeys.conf
sudo systemctl daemon-reload
sudo systemctl restart mayhome-center.service
```

Review any existing `passkeys.conf` before installing; preserve local overrides.
The private auth directory is separate from root-owned hardware history. Never
make `/var/lib/mayhome-center` writable to the web service. No backup mount, engine,
collector, Docker or tunnel permission is added. Preserve the auth directory
securely across releases; it contains credential identities and public keys.

1. Log in through Access and the existing MHC token, then open `/admin`.
2. Enter the token again, name your device and select **Thêm passkey**.
3. Log out; select **Đăng nhập bằng passkey** and verify with the device.
4. Register another device, verify both, revoke one and confirm it can no longer
   log in (including its existing session). Confirm token recovery still works.
5. Confirm unknown credentials, cancelled dialogs and wrong origins never log in.

To disable: remove only the passkeys drop-in, daemon-reload and restart MHC web.
Keep the auth database for later re-enabling. Disabling this feature does not
revoke existing sessions; rotate the existing session secret if all sessions must
be invalidated. Do not paste tokens, credentials or database contents into chat.
