'use strict';
(() => {
  const root = document.querySelector('#passkeys');
  if (!root) return;
  const message = document.querySelector('#passkey-message');
  const decode = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
  const encode = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const token = () => document.querySelector('#admin-token')?.value || '';
  async function call(action, body = {}) {
    const response = await fetch('/auth/' + action, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}, body: JSON.stringify(body)});
    if (!response.ok) throw new Error('Không hoàn tất. Kiểm tra khóa truy cập hoặc thử lại.');
    return response.json();
  }
  async function run(register) {
    if (!window.PublicKeyCredential || !window.isSecureContext) throw new Error('Trình duyệt cần hỗ trợ passkey trên HTTPS.');
    const mode = register ? 'register' : 'login';
    const options = await call('passkeys/' + mode + '-options', register ? {token: token()} : {});
    options.challenge = decode(options.challenge);
    if (register) options.user.id = decode(options.user.id);
    for (const field of ['allowCredentials', 'excludeCredentials'])
      if (options[field]) options[field] = options[field].map(c => ({...c, id: decode(c.id)}));
    const key = await navigator.credentials[register ? 'create' : 'get']({publicKey: options});
    const response = {clientDataJSON: encode(key.response.clientDataJSON)};
    if (register) response.attestationObject = encode(key.response.attestationObject);
    else Object.assign(response, {authenticatorData: encode(key.response.authenticatorData), signature: encode(key.response.signature), userHandle: key.response.userHandle ? encode(key.response.userHandle) : null});
    await call('passkeys/' + mode + '-verify', {credential: {id: key.id, rawId: encode(key.rawId), type: key.type, response}, token: register ? token() : undefined, label: document.querySelector('#passkey-label')?.value});
    location.href = register ? '/admin' : '/';
  }
  function bind(node, action) {
    node?.addEventListener('click', async () => {
      node.disabled = true;
      try { await action(); } catch (error) { message.textContent = error.name === 'NotAllowedError' ? 'Đã hủy hoặc hết thời gian xác thực.' : error.message; }
      finally { node.disabled = false; }
    });
  }
  bind(document.querySelector('#login-passkey'), () => run(false));
  bind(document.querySelector('#register-passkey'), () => run(true));
  bind(document.querySelector('#logout'), async () => { await call('logout'); location.href = '/login'; });
  document.querySelectorAll('[data-revoke]').forEach(button => bind(button, async () => {
    if (!confirm('Thu hồi passkey này?')) return;
    await call('passkeys/revoke', {id: button.dataset.revoke, token: token()}); location.reload();
  }));
})();
