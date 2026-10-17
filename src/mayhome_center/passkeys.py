"""Owner-only WebAuthn endpoints. Challenges are server-side and single use."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from flask import abort, jsonify, render_template, request, session
from webauthn import (generate_registration_options, generate_authentication_options,
    options_to_json, verify_registration_response, verify_authentication_response)
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, ResidentKeyRequirement,
    UserVerificationRequirement, PublicKeyCredentialDescriptor)


def install(app):
    @contextmanager
    def database():
        db = sqlite3.connect(app.config['AUTH_DB'], timeout=3)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def owner():
        if not session.get('authenticated'):
            abort(401)
        # Legacy token sessions are owners; passkey sessions must remain unrevoked.
        credential = session.get('passkey_id')
        if credential:
            with database() as db:
                if not db.execute('SELECT 1 FROM credentials WHERE id=?', (credential,)).fetchone():
                    session.clear()
                    abort(401)

    def csrf():
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
            abort(403)
        if request.headers.get('Origin') != app.config['PASSKEY_ORIGIN']:
            abort(403)

    def reauthenticate():
        owner()
        token = (request.get_json() or {}).get('token', '')
        if not isinstance(token, str) or not app.config['AUTH_TOKEN'] or not hmac.compare_digest(token.encode(), app.config['AUTH_TOKEN'].encode()):
            abort(403)

    def challenge(db, kind):
        session.setdefault('auth_binding', secrets.token_urlsafe(32))
        binding = hashlib.sha256(session['auth_binding'].encode()).hexdigest()
        db.execute('DELETE FROM challenges WHERE expires < ? OR binding=?', (time.time(), binding))
        if db.execute('SELECT count(*) FROM challenges').fetchone()[0] >= 1024:
            abort(429)
        value = secrets.token_bytes(32)
        db.execute('INSERT INTO challenges VALUES (?,?,?,?)', (binding, kind, value, time.time()+120))
        return value

    def consume(db, kind):
        binding = hashlib.sha256(session.get('auth_binding', '').encode()).hexdigest()
        row = db.execute('SELECT * FROM challenges WHERE binding=?', (binding,)).fetchone()
        db.execute('DELETE FROM challenges WHERE binding=?', (binding,))
        # Commit consumption before signature validation; failures cannot replay.
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        if not row or row['kind'] != kind or row['expires'] < time.time():
            abort(400)
        return row['value']

    @app.get('/admin')
    def admin():
        owner()
        session.setdefault('csrf', secrets.token_urlsafe(32))
        if not app.config['PASSKEY_ENABLED']:
            return render_template('admin.html', enabled=False, credentials=[])
        with database() as db:
            rows = db.execute('SELECT id,label,created FROM credentials ORDER BY created').fetchall()
        return render_template('admin.html', enabled=True, credentials=rows)

    @app.post('/auth/logout')
    def logout():
        csrf()
        session.clear()
        return jsonify(ok=True)

    @app.post('/auth/passkeys/<action>')
    def passkey(action):
        if not app.config['PASSKEY_ENABLED']:
            abort(404)
        csrf()
        with database() as db:
            window = int(time.time() // 300)
            db.execute('DELETE FROM rate_limits WHERE window != ?', (window,))
            db.execute('INSERT INTO rate_limits VALUES (?,1) ON CONFLICT(window) DO UPDATE SET count=count+1', (window,))
            limited = db.execute('SELECT count FROM rate_limits WHERE window=?', (window,)).fetchone()[0] > 100
        if limited:
            abort(429)
        if action not in ('login-options', 'login-verify', 'register-options', 'register-verify', 'revoke'):
            abort(404)
        if not app.config['AUTH_TOKEN']:
            abort(503)
        if action in ('register-options', 'register-verify', 'revoke'):
            reauthenticate()
        with database() as db:
            if action == 'revoke':
                db.execute('DELETE FROM credentials WHERE id=?', ((request.get_json() or {}).get('id', ''),))
                return jsonify(ok=True)
            if action.endswith('options'):
                kind = action.split('-')[0]
                value = challenge(db, kind)
                if kind == 'register':
                    if db.execute('SELECT count(*) FROM credentials').fetchone()[0] >= 20:
                        abort(409)
                    options = generate_registration_options(rp_id=app.config['PASSKEY_RP_ID'], rp_name='MAYHOME',
                        user_id=b'mayhome-owner-v1', user_name='owner', challenge=value,
                        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                            user_verification=UserVerificationRequirement.REQUIRED),
                        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r['id'])) for r in db.execute('SELECT id FROM credentials')])
                else:
                    options = generate_authentication_options(rp_id=app.config['PASSKEY_RP_ID'], challenge=value,
                        user_verification=UserVerificationRequirement.REQUIRED)
                return jsonify(json.loads(options_to_json(options)))
            kind = action.split('-')[0]
            expected = consume(db, kind)
            body = request.get_json() or {}
            credential = body.get('credential', {})
            try:
                if kind == 'register':
                    verified = verify_registration_response(credential=credential, expected_challenge=expected,
                        expected_rp_id=app.config['PASSKEY_RP_ID'], expected_origin=app.config['PASSKEY_ORIGIN'], require_user_verification=True)
                    label = body.get('label', 'Passkey')
                    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
                        abort(400)
                    db.execute('INSERT INTO credentials VALUES (?,?,?,?,?)', (bytes_to_base64url(verified.credential_id), label.strip(),
                        verified.credential_public_key, verified.sign_count, int(time.time())))
                else:
                    row = db.execute('SELECT * FROM credentials WHERE id=?', (credential.get('id', ''),)).fetchone()
                    if not row:
                        abort(400)
                    verified = verify_authentication_response(credential=credential, expected_challenge=expected,
                        expected_rp_id=app.config['PASSKEY_RP_ID'], expected_origin=app.config['PASSKEY_ORIGIN'],
                        credential_public_key=row['public_key'], credential_current_sign_count=row['counter'], require_user_verification=True)
                    handle = credential.get('response', {}).get('userHandle')
                    if handle and base64url_to_bytes(handle) != b'mayhome-owner-v1':
                        abort(400)
                    db.execute('UPDATE credentials SET counter=? WHERE id=?', (verified.new_sign_count, row['id']))
                    session.clear()
                    session.update(authenticated=True, passkey_id=row['id'], csrf=secrets.token_urlsafe(32))
                    session.permanent = True
            except (ValueError, TypeError, KeyError, sqlite3.IntegrityError):
                abort(400)
            except Exception as exc:
                from werkzeug.exceptions import HTTPException
                if isinstance(exc, HTTPException):
                    raise
                abort(400)
        return jsonify(ok=True)

    app.extensions['passkey_owner_check'] = owner


def initialize(path):
    """Run once as service user in its private state directory."""
    import os
    os.umask(0o077)
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS credentials (
          id TEXT PRIMARY KEY,label TEXT NOT NULL,public_key BLOB NOT NULL,counter INTEGER NOT NULL,created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS rate_limits (window INTEGER PRIMARY KEY,count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS challenges (
          binding TEXT PRIMARY KEY,kind TEXT NOT NULL,value BLOB NOT NULL,expires REAL NOT NULL);
        ''')
