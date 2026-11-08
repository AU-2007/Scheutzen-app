// Push-Benachrichtigungen auf diesem Gerät ein- und ausschalten
(function () {
  var box = document.getElementById('push-box');
  if (!box) return;
  var on = document.getElementById('push-on'), off = document.getElementById('push-off'), state = document.getElementById('push-state');
  function csrf() { var m = document.querySelector('meta[name=csrf]'); return m ? m.content : ''; }
  function post(url, body) { return fetch(url, { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: JSON.stringify(body) }).then(function (r) { return r.json(); }); }
  function key(b64) { b64 = b64.replace(/-/g, '+').replace(/_/g, '/'); while (b64.length % 4) b64 += '=';
    var s = atob(b64), a = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; }
  function show(text, isOn) { state.textContent = text; on.hidden = !!isOn; off.hidden = !isOn; }
  var standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  var ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    show(ios && !standalone ? 'Auf dem iPhone: erst die App zum Home-Bildschirm hinzufügen (Teilen → „Zum Home-Bildschirm“) und dort öffnen.'
                            : 'Dieser Browser kann keine Benachrichtigungen.', false);
    on.hidden = true; return;
  }
  navigator.serviceWorker.register('/sw.js').then(function (reg) {
    return reg.pushManager.getSubscription().then(function (sub) {
      if (sub && Notification.permission === 'granted') { post('/push/abo', sub.toJSON()); show('Auf diesem Gerät eingeschaltet.', true); }
      else if (Notification.permission === 'denied') { show('Benachrichtigungen sind für diese Seite blockiert. Bitte in den Browser-Einstellungen erlauben.', false); }
      else show('Auf diesem Gerät aus.', false);
      on.addEventListener('click', function () {
        on.disabled = true;
        Notification.requestPermission().then(function (p) {
          if (p !== 'granted') { show('Nicht erlaubt. Bitte in den Browser-Einstellungen erlauben.', false); on.disabled = false; return; }
          return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key(box.dataset.key) }).then(function (s) {
            return post('/push/abo', s.toJSON()).then(function (r) {
              show(r.ok ? 'Auf diesem Gerät eingeschaltet.' : (r.msg || 'Hat nicht geklappt.'), r.ok); on.disabled = false;
              if (r.ok) document.getElementById('push-test').hidden = false;
            });
          });
        }).catch(function (e) { show('Hat nicht geklappt: ' + e.message, false); on.disabled = false; });
      });
      off.addEventListener('click', function () {
        reg.pushManager.getSubscription().then(function (s) {
          if (!s) return show('Auf diesem Gerät aus.', false);
          return post('/push/abmelden', { endpoint: s.endpoint }).then(function () { return s.unsubscribe(); })
            .then(function () { show('Auf diesem Gerät aus.', false); });
        });
      });
    });
  }).catch(function () { show('Benachrichtigungen gehen nur über HTTPS.', false); on.hidden = true; });
})();
