/* Buttons and fields without inline JavaScript.
   The page's security policy forbids code inside the HTML (onclick="…", <script>…</script>), so a page can never
   run anything that sneaks into it. Elements name what they do with data attributes instead:
     data-click="openDlg" data-args='["about-dlg"]'   ·   data-change / data-input / data-submit likewise
     data-confirm="¿Seguro?"   asks before a click or a form submission goes ahead
   Special arguments: "$this" (the element), "$value", "$checked", "$event". A function returning false cancels
   the default action, and a link with href="#" never jumps. */
(function () {
  var ACTIONS = {
    closeDialog: function (el) { var d = el.closest('dialog'); if (d) d.close(); },
    closeId: function (id) { var d = document.getElementById(id); if (d && d.close) d.close(); },
    openTabChooser: function () { var c = document.querySelector('.tab-chooser'); if (c) c.open = true; },
    toggleSimple: function () { setSimple(!isSimple()); },
    toggleFullscreen: function () {
      if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen();
    },
    clearUrl: function () { var u = document.getElementById('url'); u.value = ''; u.focus(); },
    clearRobots: function () { var t = document.getElementById('custom_robots'); t.value = ''; t.dispatchEvent(new Event('input')); },
    uaToggle: function (el) { document.getElementById('ua_custom_wrap').hidden = el.value !== 'custom'; },
    submitForm: function (el) { el.form.submit(); },
    langGo: function (code) {
      location.href = '/lang/' + code + '?next=' + encodeURIComponent(location.pathname + location.search);
      return false;
    },
    htmxLoad: function (url, target, swap) { htmx.ajax('GET', url, {target: target, swap: swap || 'outerHTML'}); },
    goBack: function () { history.back(); return false; }
  };

  function arg(a, el, e) {
    if (a === '$this') return el;
    if (a === '$value') return el.value;
    if (a === '$checked') return el.checked;
    if (a === '$event') return e;
    return a;
  }

  function run(el, type, e) {
    var name = el.getAttribute('data-' + type), fn = ACTIONS[name] || window[name];
    if (el.tagName === 'A' && el.getAttribute('href') === '#') e.preventDefault();
    if (typeof fn !== 'function') { if (window.console) console.warn('LetsCrawlit: acción desconocida', name); return; }
    var args = [];
    try { args = JSON.parse(el.getAttribute('data-args') || '[]'); } catch (x) { args = []; }
    if (fn.apply(el, args.map(function (a) { return arg(a, el, e); })) === false) e.preventDefault();
  }

  ['click', 'change', 'input', 'submit'].forEach(function (type) {
    document.addEventListener(type, function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      if (type === 'click' || type === 'submit') {
        var c = type === 'submit' ? t.closest('form[data-confirm]') : t.closest('[data-confirm]:not(form)');
        if (c && !window.confirm(c.getAttribute('data-confirm'))) { e.preventDefault(); e.stopImmediatePropagation(); return; }
      }
      var el = t.closest('[data-' + type + ']');
      if (el) run(el, type, e);
    }, true);
  });
})();

// Texts written by scripts, in the interface language (the server fills #lc-i18n when the page is in English).
var LC_I18N = null;
function lcT(s) {
  if (LC_I18N === null) {
    try { LC_I18N = JSON.parse((document.getElementById('lc-i18n') || {}).textContent || '{}'); } catch (e) { LC_I18N = {}; }
  }
  return LC_I18N[s] || s;
}
// Values a page passes to its script (crawl id, sections…), from <script type="application/json" id="page-data">.
function pageData() {
  try { return JSON.parse((document.getElementById('page-data') || {}).textContent || '{}'); } catch (e) { return {}; }
}

// Menu bar: dialogs and one open menu at a time, like a desktop menu bar.
function openDlg(id) { var d = document.getElementById(id); if (d && !d.open) d.showModal(); document.querySelectorAll('details.mb[open]').forEach(function (m) { m.open = false; }); }
document.addEventListener('click', function (e) {
  var picked = e.target.closest && e.target.closest('.mb-list .mi:not(.disabled)');  // a menu item closes its menu
  document.querySelectorAll('details.mb[open]').forEach(function (m) { if (picked || !m.contains(e.target)) m.open = false; });
});
document.addEventListener('toggle', function (e) {  // only one menu open at a time, like a desktop menu bar
  if (e.target.matches && e.target.matches('details.mb') && e.target.open)
    document.querySelectorAll('details.mb[open]').forEach(function (m) { if (m !== e.target) m.open = false; });
}, true);
// The language menu closes on a click anywhere else, like the menu bar.
document.addEventListener('click', function (e) {
  document.querySelectorAll('details.lang-sw[open]').forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
});
// Loading state (rule 19): the button that sent a form shows it is working and cannot be pressed twice.
// Pages that stay where they are (a PDF download) get the button back after a few seconds.
document.addEventListener('submit', function (e) {
  if (e.defaultPrevented || e.target.method === 'dialog') return;
  var b = e.submitter || e.target.querySelector('button[type=submit], button:not([type])');
  if (!b || b.classList.contains('is-busy')) return;
  setTimeout(function () { b.classList.add('is-busy'); b.setAttribute('aria-busy', 'true'); }, 0);
  setTimeout(function () { b.classList.remove('is-busy'); b.removeAttribute('aria-busy'); }, 6000);
});
window.addEventListener('pageshow', function () {  // coming back with the browser's Back button
  document.querySelectorAll('.is-busy').forEach(function (b) { b.classList.remove('is-busy'); b.removeAttribute('aria-busy'); });
});

// Ayuda › Diagnóstico › Copiar
function copyDiag() {
  var t = document.getElementById('diag-text'), ok = document.getElementById('diag-ok');
  var done = function () { if (ok) ok.textContent = lcT('Copiado'); };
  if (navigator.clipboard) navigator.clipboard.writeText(t.value).then(done, function () { t.select(); document.execCommand('copy'); done(); });
  else { t.select(); document.execCommand('copy'); done(); }
}
// Modo: the current mode is marked in the menu (start page)
document.addEventListener('DOMContentLoaded', function () {
  var m = document.getElementById('mode');
  function mark() { document.querySelectorAll('.mode-mi').forEach(function (b) { b.classList.toggle('on', m && b.dataset.mode === m.value); }); }
  if (m) { mark(); m.addEventListener('change', mark); document.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('.mode-mi')) setTimeout(mark, 0); }); }
});

// A dialog opened with the mouse puts the focus on the dialog itself, not on its «Cerrar» button
// (keyboard users still Tab straight to it), so it does not open with a highlighted button.
(function () {
  var show = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal = function () {
    show.apply(this, arguments);
    var a = document.activeElement;
    if (a && a.getAttribute && a.getAttribute('aria-label') === 'Cerrar' && !this.querySelector('[autofocus]')) {
      if (!this.hasAttribute('tabindex')) this.setAttribute('tabindex', '-1');
      this.focus({preventScroll: true});
    }
  };
})();

// «Exportar» menu in the toolbar: closes on a click outside or after choosing
document.addEventListener('click', function (e) {
  document.querySelectorAll('details.exp-menu[open]').forEach(function (d) {
    if (!d.contains(e.target) || (e.target.closest && e.target.closest('.exp-list .mi'))) d.open = false;
  });
});
// Vista menu: toggles show whether they are on (beginner mode, AI button)
function syncViewToggles() {
  var simple = document.body.classList.contains('simple'), fab = !document.body.classList.contains('no-fab');
  document.querySelectorAll('.mb-list [data-click="toggleSimple"]').forEach(function (b) { b.classList.toggle('on', simple); b.setAttribute('aria-checked', simple); b.setAttribute('role', 'menuitemcheckbox'); });
  document.querySelectorAll('.mb-list [data-click="toggleConsultFab"]').forEach(function (b) { b.classList.toggle('on', fab); b.setAttribute('aria-checked', fab); b.setAttribute('role', 'menuitemcheckbox'); });
  var upd = updatesOn();
  document.querySelectorAll('.mb-list [data-click="toggleUpdateNotice"]').forEach(function (b) { b.classList.toggle('on', upd); b.setAttribute('aria-checked', upd); b.setAttribute('role', 'menuitemcheckbox'); });
}
document.addEventListener('DOMContentLoaded', function () { setTimeout(syncViewToggles, 0); });
document.addEventListener('letscrawl:simple', syncViewToggles);
document.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('[data-click="toggleConsultFab"], [data-click="toggleSimple"], [data-click="toggleUpdateNotice"]')) setTimeout(syncViewToggles, 0); });

// «Hay una versión nueva»: once per page load the app asks its own server, which asks GitHub at most every 12 hours.
// Vista › Avisar de versiones nuevas turns it off. «Ahora no» hides the notice until the next version.
var UPD_OFF = 'letscrawl.noUpdates', UPD_SEEN = 'letscrawl.updateDismissed';
function updatesOn() { try { return localStorage.getItem(UPD_OFF) !== '1'; } catch (e) { return true; } }
function toggleUpdateNotice() {
  try { if (updatesOn()) localStorage.setItem(UPD_OFF, '1'); else localStorage.removeItem(UPD_OFF); } catch (e) {}
  if (!updatesOn()) { var n = document.getElementById('upd-note'); if (n) n.remove(); } else checkUpdates();
  document.querySelectorAll('details.mb[open]').forEach(function (m) { m.open = false; });
}
function dismissUpdate(version) {
  try { localStorage.setItem(UPD_SEEN, version); } catch (e) {}
  var n = document.getElementById('upd-note'); if (n) n.remove();
}
function checkUpdates() {
  if (!updatesOn() || !window.fetch || document.getElementById('upd-note')) return;
  fetch('/updates').then(function (r) { return r.ok ? r.json() : null; }).then(function (u) {
    if (!u || !u.newer || !u.latest) return;
    var seen = ''; try { seen = localStorage.getItem(UPD_SEEN) || ''; } catch (e) {}
    if (seen === u.latest || document.getElementById('upd-note')) return;
    var box = document.createElement('div');
    box.id = 'upd-note'; box.className = 'upd-note'; box.setAttribute('role', 'status');
    var txt = document.createElement('span');
    txt.textContent = lcT('Hay una versión nueva de LetsCrawlit: ') + u.latest + lcT('. Tienes la ') + u.current + '.';
    var get = document.createElement('a');
    get.className = 'btn'; get.href = u.url; get.target = '_blank'; get.rel = 'noopener'; get.textContent = lcT('Descargar');
    var later = document.createElement('button');
    later.type = 'button'; later.className = 'secondary'; later.textContent = lcT('Ahora no');
    later.addEventListener('click', function () { dismissUpdate(u.latest); });
    box.appendChild(txt); box.appendChild(get); box.appendChild(later);
    var bar = document.querySelector('.menubar') || document.querySelector('header');
    if (bar && bar.parentNode) bar.parentNode.insertBefore(box, bar.nextSibling); else document.body.insertBefore(box, document.body.firstChild);
  }).catch(function () {});
}
document.addEventListener('DOMContentLoaded', function () { setTimeout(checkUpdates, 1500); });
