import json
import pytest
from mayhome_center.app import create_app
from mayhome_center.passkeys import initialize

@pytest.fixture
def client(tmp_path):
    path=tmp_path/'auth.db'
    initialize(path)
    app=create_app({'TESTING':True,'SECRET_KEY':'test-secret','AUTH_TOKEN':'owner-token','PASSKEY_ENABLED':True,'AUTH_DB':str(path),'SESSION_COOKIE_SECURE':False})
    return app.test_client()

def headers(client, owner=False):
    with client.session_transaction() as s:
        s['csrf']='csrf'
        if owner:s['authenticated']=True
    return {'X-CSRF-Token':'csrf','Origin':'https://mayhome.cuong.win'}

def test_admin_requires_login(client):
    assert client.get('/admin').status_code==302

def test_register_requires_owner_token_and_csrf(client):
    h=headers(client,True)
    for body in ({},{'token':'wrong'}):
        assert client.post('/auth/passkeys/register-options',headers=h,json=body).status_code==403
    assert client.post('/auth/passkeys/register-options',json={'token':'owner-token'}).status_code==403
    h['Origin']='https://evil.example'
    assert client.post('/auth/passkeys/register-options',headers=h,json={'token':'owner-token'}).status_code==403

def test_options_require_uv_and_resident_key(client):
    h=headers(client,True)
    r=client.post('/auth/passkeys/register-options',headers=h,json={'token':'owner-token'})
    assert r.status_code==200
    assert r.json['authenticatorSelection']['userVerification']=='required'
    assert r.json['authenticatorSelection']['residentKey']=='required'
    assert r.json['rp']['id']=='mayhome.cuong.win'
    assert client.get('/admin').status_code==200

def test_bad_credential_consumes_challenge(client):
    h=headers(client)
    assert client.post('/auth/passkeys/login-options',headers=h,json={}).status_code==200
    assert client.post('/auth/passkeys/login-verify',headers=h,json={'credential':{}}).status_code==400
    import sqlite3
    with sqlite3.connect(client.application.config['AUTH_DB']) as db:
        assert db.execute('select count(*) from challenges').fetchone()[0]==0
    assert client.get('/').status_code==302

def test_logout_and_disabled(client):
    h=headers(client,True)
    assert client.post('/auth/logout',headers=h,json={}).status_code==200
    assert client.get('/admin').status_code==302
    client.application.config['PASSKEY_ENABLED']=False
    assert client.post('/auth/passkeys/login-options',headers=headers(client),json={}).status_code==404

def test_revoked_session_denied(client):
    headers(client,True)
    with client.session_transaction() as s:s['passkey_id']='revoked'
    assert client.get('/api/v1/overview').status_code==401

@pytest.mark.parametrize("failure", [None, "origin", "uv", "signature"])
def test_real_registration_login_and_replay(client, failure):
    import hashlib, struct, cbor2
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
    from webauthn.helpers import bytes_to_base64url as enc, base64url_to_bytes as dec
    key=ec.generate_private_key(ec.SECP256R1())
    pub=key.public_key().public_numbers()
    credential_id=b'synthetic-test-credential'
    rp=hashlib.sha256(b'mayhome.cuong.win').digest()
    h=headers(client,True)
    options=client.post('/auth/passkeys/register-options',headers=h,json={'token':'owner-token'}).json
    data=json.dumps({'type':'webauthn.create','challenge':options['challenge'],'origin':'https://mayhome.cuong.win'}).encode()
    cose=cbor2.dumps({1:2,3:-7,-1:1,-2:pub.x.to_bytes(32,'big'),-3:pub.y.to_bytes(32,'big')})
    auth=rp+b'\x45'+struct.pack('>I',0)+b'\0'*16+struct.pack('>H',len(credential_id))+credential_id+cose
    credential={'id':enc(credential_id),'rawId':enc(credential_id),'type':'public-key','response':{'clientDataJSON':enc(data),'attestationObject':enc(cbor2.dumps({'fmt':'none','attStmt':{},'authData':auth}))}}
    response=client.post('/auth/passkeys/register-verify',headers=h,json={'token':'owner-token','label':'Synthetic key','credential':credential})
    assert response.status_code==200
    assert client.post('/auth/logout',headers=h,json={}).status_code==200
    h=headers(client)
    options=client.post('/auth/passkeys/login-options',headers=h,json={}).json
    data=json.dumps({'type':'webauthn.get','challenge':options['challenge'],'origin':'https://evil.example' if failure=='origin' else 'https://mayhome.cuong.win'}).encode()
    auth=rp+(b'\x01' if failure=='uv' else b'\x05')+struct.pack('>I',1)
    credential={'id':enc(credential_id),'rawId':enc(credential_id),'type':'public-key','response':{'clientDataJSON':enc(data),'authenticatorData':enc(auth),'signature':enc(key.sign(auth+hashlib.sha256(data).digest(),ec.ECDSA(hashes.SHA256()))),'userHandle':enc(b'mayhome-owner-v1')}}
    if failure=='signature': credential['response']['signature']=enc(b'invalid')
    response=client.post('/auth/passkeys/login-verify',headers=h,json={'credential':credential})
    if failure:
        assert response.status_code==400
        assert client.get('/admin').status_code==302
        return
    assert response.status_code==200
    with client.session_transaction() as s:
        assert s['authenticated'] and s['passkey_id']==enc(credential_id)
        h['X-CSRF-Token']=s['csrf']
    assert client.post('/auth/passkeys/login-verify',headers=h,json={'credential':credential}).status_code==400
    assert client.get('/admin').status_code==200
    assert client.post('/auth/passkeys/revoke',headers=h,json={'token':'owner-token','id':enc(credential_id)}).status_code==200
    assert client.get('/admin').status_code==401
