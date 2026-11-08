// Passkeys (WebAuthn) – Anmelden und Einrichten
(function () {
  function b2u(buf) { var s = '', b = new Uint8Array(buf); for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
  function u2b(str) { str = str.replace(/-/g, '+').replace(/_/g, '/'); while (str.length % 4) str += '=';
    var s = atob(str), b = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) b[i] = s.charCodeAt(i); return b.buffer; }
  function csrf() { var m = document.querySelector('meta[name=csrf]'); return m ? m.content : ''; }
  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function msg(el, text) { var m = el.parentNode.querySelector('.pk-msg'); if (m) { m.textContent = text; m.hidden = !text; } }
  var supported = !!(window.PublicKeyCredential && navigator.credentials);

  // Anmelden (ohne Passwort) oder als zweiter Schritt
  document.querySelectorAll('[data-passkey-login]').forEach(function (btn) {
    if (!supported) { btn.hidden = true; return; }
    btn.hidden = false;
    btn.addEventListener('click', function () {
      msg(btn, '');
      btn.disabled = true;
      post(btn.dataset.optionsUrl).then(function (o) {
        var pk = { challenge: u2b(o.challenge), rpId: o.rpId, timeout: o.timeout, userVerification: o.userVerification,
                   allowCredentials: (o.allowCredentials || []).map(function (c) { return { type: c.type, id: u2b(c.id) }; }) };
        return navigator.credentials.get({ publicKey: pk });
      }).then(function (c) {
        return post(btn.dataset.passkeyLogin, { id: c.id, rawId: b2u(c.rawId), type: c.type, next: btn.dataset.next || '',
          response: { clientDataJSON: b2u(c.response.clientDataJSON), authenticatorData: b2u(c.response.authenticatorData),
                      signature: b2u(c.response.signature), userHandle: c.response.userHandle ? b2u(c.response.userHandle) : null } });
      }).then(function (r) {
        if (r.ok) { location.href = r.next || '/'; return; }
        msg(btn, r.msg || 'Anmeldung mit Passkey hat nicht geklappt.'); btn.disabled = false;
      }).catch(function (e) {
        msg(btn, e && e.name === 'NotAllowedError' ? 'Abgebrochen.' : 'Anmeldung mit Passkey hat nicht geklappt.');
        btn.disabled = false;
      });
    });
  });

  // Neuen Passkey einrichten
  document.querySelectorAll('[data-passkey-register]').forEach(function (btn) {
    if (!supported) { msg(btn, 'Dieser Browser kann keine Passkeys.'); btn.disabled = true; return; }
    btn.addEventListener('click', function () {
      msg(btn, ''); btn.disabled = true;
      var nameField = document.getElementById(btn.dataset.nameField || '');
      post(btn.dataset.optionsUrl).then(function (o) {
        var pk = { challenge: u2b(o.challenge), rp: o.rp, timeout: o.timeout, attestation: o.attestation,
                   user: { id: u2b(o.user.id), name: o.user.name, displayName: o.user.displayName },
                   pubKeyCredParams: o.pubKeyCredParams, authenticatorSelection: o.authenticatorSelection,
                   excludeCredentials: o.excludeCredentials.map(function (c) { return { type: c.type, id: u2b(c.id) }; }) };
        return navigator.credentials.create({ publicKey: pk });
      }).then(function (c) {
        return post(btn.dataset.passkeyRegister, { id: c.id, rawId: b2u(c.rawId), type: c.type,
          name: nameField ? nameField.value : '',
          response: { clientDataJSON: b2u(c.response.clientDataJSON), attestationObject: b2u(c.response.attestationObject) } });
      }).then(function (r) {
        if (r.ok) { location.reload(); return; }
        msg(btn, r.msg || 'Hat nicht geklappt.'); btn.disabled = false;
      }).catch(function (e) {
        msg(btn, e && e.name === 'InvalidStateError' ? 'Dieses Gerät ist schon eingerichtet.' :
                 e && e.name === 'NotAllowedError' ? 'Abgebrochen.' : 'Hat nicht geklappt: ' + (e && e.message || e));
        btn.disabled = false;
      });
    });
  });
})();
