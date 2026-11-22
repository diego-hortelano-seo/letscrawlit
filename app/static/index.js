/* Start page (index.html): 1-click audits, mode switch and the configuration dialog. */
// One-click audits: tune the configuration panel for one job, then start (the permission box stays the user's).
var QUICK = {
  full:   {label: lcT('Auditoría completa'), set: {}},
  broken: {label: lcT('Solo enlaces rotos'), set: {check_external: true, store_external: true, check_resources: true, crawl_images: true,
           crawl_css: false, crawl_js: false, crawl_media: false, render: false, extract_readability: false, extract_text_ratio: false,
           spelling: false, html_validation: false, carbon: false, ext_a11y: false, ext_schema_validation: false}},
  images: {label: lcT('Solo imágenes pesadas'), set: {crawl_images: true, store_images: true, check_resources: true, extract_srcset: true,
           crawl_css: false, crawl_js: false, crawl_media: false, check_external: false, extract_readability: false,
           spelling: false, html_validation: false, carbon: false}},
  sitemap: {label: lcT('Solo el sitemap'), set: {}}
};
var QUICK_BASE = Array.prototype.map.call(document.querySelectorAll('#crawl-form input[type=checkbox][name]:not([name=ack])'),
  function (el) { return [el, el.checked]; });
function quickAudit(kind) {
  var msg = document.getElementById('quick-msg'), url = document.getElementById('url'), q = QUICK[kind];
  var raw = (url.value || '').trim();
  if (!raw) { msg.textContent = lcT('Primero escribe arriba la URL de la web que quieres revisar.'); url.focus(); return; }
  if (!/^https?:\/\//i.test(raw)) { raw = 'https://' + raw; url.value = raw; }
  QUICK_BASE.forEach(function (b) { b[0].checked = b[1]; });  // every button starts from the saved configuration
  if (window.applyScope) applyScope();  // …but keeps the scope chosen next to the URL
  Object.keys(q.set).forEach(function (name) {
    var el = document.querySelector('#crawl-form input[type=checkbox][name="' + name + '"]');
    if (el && !el.disabled) el.checked = q.set[name];
  });
  document.getElementById('preset').value = kind === 'full' ? '' : kind;
  if (kind === 'sitemap') {
    var origin; try { origin = new URL(raw).origin; } catch (e) { msg.textContent = lcT('La URL no es válida.'); return; }
    document.getElementById('urls').value = origin + '/sitemap.xml';
    document.getElementById('mode').value = 'list';
    document.getElementById('mode-spider').hidden = true; document.getElementById('mode-list-bar').hidden = false;
    var sc1 = document.getElementById('scope'); if (sc1) sc1.hidden = true;  // a list has no scope
  } else if (document.getElementById('mode').value === 'list') {
    document.getElementById('mode').value = 'spider';
    document.getElementById('mode-spider').hidden = false; document.getElementById('mode-list-bar').hidden = true;
    var sc2 = document.getElementById('scope'); if (sc2) sc2.hidden = false;
  }
  var ack = document.querySelector('#crawl-form input[name=ack]');
  if (!ack.checked) {
    msg.textContent = '«' + q.label + lcT('» preparada. Marca «Tengo permiso» y pulsa Rastrear.');
    ack.closest('label').classList.add('attn'); ack.focus(); return;
  }
  msg.textContent = lcT('Empezando «') + q.label + '»…';
  document.getElementById('crawl-form').requestSubmit();
}
function setMode(m) {
  document.getElementById('mode').value = m;
  document.getElementById('mode-spider').hidden = m !== 'spider';
  document.getElementById('mode-list-bar').hidden = m !== 'list';
  var sc = document.getElementById('scope'); if (sc) sc.hidden = m === 'list';
  document.querySelectorAll('details.mb[open]').forEach(function (d) { d.open = false; });
  if (m === 'list') openDlg('list-dlg');
}
var CFG_ALIAS = {resources: 'crawl', behaviour: 'crawl', crawl_store: 'crawl', ext: 'extraction', thresholds: 'prefs'};
function showSec(key) {
  key = CFG_ALIAS[key] || key;
  document.querySelectorAll('.cfg-body section').forEach(function (s) { s.classList.toggle('on', s.dataset.sec === key); });
  var crumb = '';
  document.querySelectorAll('.cfg-nav a[data-sec]').forEach(function (a) {
    var on = a.dataset.sec === key;
    a.classList.toggle('on', on);
    if (on) {
      crumb += (crumb ? ' › ' : '') + a.dataset.crumb;
      if (a.dataset.parent) toggleGrp(a.dataset.parent, true);
    }
  });
  document.getElementById('cfg-crumb').textContent = crumb;
  document.querySelector('.cfg-body').scrollTop = 0;
}
function toggleGrp(key, open) {
  var g = document.querySelector('.cfg-nav a[data-grp="' + key + '"]');
  var kids = document.querySelectorAll('.cfg-nav a[data-parent="' + key + '"]');
  var collapsed = g.classList.contains('collapsed');
  if (open === true) { if (!collapsed) return; }
  else if (!collapsed && open === undefined && !Array.prototype.some.call(kids, function (k) { return k.classList.contains('on'); })) {
    showSec(kids[0].dataset.sec); return;  // clicking a group the first time opens its first page
  }
  var hide = open === true ? false : !collapsed;
  g.classList.toggle('collapsed', hide);
  g.querySelector('.tw').textContent = hide ? '▸' : '▾';
  kids.forEach(function (k) { k.classList.toggle('folded', hide); });
}
// «Cancelar» puts every field back the way it was when the dialog was opened
var cfgSnap = null;
function snapCfg() {
  cfgSnap = Array.prototype.map.call(document.querySelectorAll('#cfg input, #cfg select, #cfg textarea'), function (el) {
    return [el, el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value, el.disabled];
  });
}
function openCfg(sec) { var d = document.getElementById('cfg'); if (!d.open) { snapCfg(); d.showModal(); } showSec(sec || pageData().firstSec || 'crawl'); }
function closeCfg() { cfgSnap = null; document.getElementById('cfg').close(); }
// Enter inside a dialog of the crawl form (Configuración, picker…) must never submit the form: it would start a new crawl
document.getElementById('crawl-form').addEventListener('keydown', function (e) {
  var t = e.target;
  if (e.key === 'Enter' && t.tagName === 'INPUT' && t.closest('dialog') && !/^(submit|button|checkbox|radio)$/.test(t.type)) e.preventDefault();
});
// opened from an audit (?cfg=…&from=<id>): closing without accepting goes back to that audit instead of the empty start page
var CFG_FROM = (new URLSearchParams(location.search).get('from') || '').replace(/\D/g, '');
if (CFG_FROM && document.getElementById('cfg-back')) document.getElementById('cfg-back').value = CFG_FROM;  // «Guardar» returns there too
function cancelCfg() {
  (cfgSnap || []).forEach(function (s) {
    var el = s[0];
    if (el.type === 'file') return;
    if (el.type === 'checkbox' || el.type === 'radio') el.checked = s[1]; else el.value = s[1];
    el.disabled = s[2];
    el.dispatchEvent(new Event('input'));
  });
  closeCfg();
  if (CFG_FROM) location.href = '/crawls/' + CFG_FROM;
}
document.getElementById('cfg').addEventListener('cancel', function (e) { e.preventDefault(); cancelCfg(); });
function limToggle(cb) { var i = document.getElementById(cb.dataset.lim); if (i) { i.disabled = !cb.checked; if (cb.checked) i.focus(); } }
(function () {  // robots.txt editor: line numbers, cursor position and the site name in the list
  function wire() {
    var t = document.getElementById('custom_robots'), g = document.getElementById('robots-gutter'), pos = document.getElementById('robots-pos');
    if (!t || t.dataset.wired) return;
    t.dataset.wired = '1';
    function upd() {
      var n = t.value.split('\n').length, out = []; for (var i = 1; i <= n; i++) out.push(i);
      g.textContent = out.join('\n'); g.scrollTop = t.scrollTop;
      var before = t.value.slice(0, t.selectionStart).split('\n');
      pos.textContent = 'Ln ' + before.length + ', Col ' + (before[before.length - 1].length + 1);
    }
    ['input', 'click', 'keyup', 'scroll'].forEach(function (ev) { t.addEventListener(ev, upd); });
    upd();
  }
  wire();
  document.addEventListener('htmx:afterSwap', wire);
  var url = document.getElementById('url'), site = document.getElementById('robots-site');
  function host() { try { site.textContent = new URL(url.value).host || lcT('(la URL escrita arriba)'); } catch (e) { site.textContent = lcT('(la URL escrita arriba)'); } }
  if (url && site) { url.addEventListener('input', host); host(); }
})();
function filterCfg(q) {
  q = q.toLowerCase();
  document.querySelectorAll('.cfg-nav a[data-sec]').forEach(function (a) {
    var sec = document.querySelector('.cfg-body section[data-sec="' + a.dataset.sec + '"]');
    a.hidden = q && !(a.textContent + ' ' + sec.textContent).toLowerCase().includes(q);
    if (q && !a.hidden && a.dataset.parent) toggleGrp(a.dataset.parent, true);
  });
  document.querySelectorAll('.cfg-nav a[data-grp]').forEach(function (g) {
    g.hidden = q && !document.querySelector('.cfg-nav a[data-parent="' + g.dataset.grp + '"]:not([hidden])');
  });
}
if (pageData().openCfg) openCfg();
(function () {  // menu links from the audit page: ?cfg=<section>, ?mode=list, ?saved=1, ?open=1
  var q = new URLSearchParams(location.search);
  if (q.get('cfg')) openCfg(q.get('cfg'));
  if (q.get('mode')) setMode(q.get('mode') === 'list' ? 'list' : 'spider');
  if (q.get('saved')) openDlg('saved-dlg');
  if (q.get('open')) openDlg('open-dlg');
})();

// Crawl scope next to the URL. It only moves the existing options in Configuración, so both always agree:
// Subdominio = the whole host · Subcarpeta = only below the start folder · Todos los subdominios · URL exacta = 1 URL.
(function () {
  var sel = document.getElementById('scope');
  if (!sel) return;
  var out = document.querySelector('#crawl-form input[name=crawl_outside_folder]');
  var sub = document.querySelector('#crawl-form input[name=crawl_subdomains]');
  var limCb = document.querySelector('#crawl-form input[data-lim=max_urls]'), lim = document.getElementById('max_urls');
  var before = null;  // the URL limit as it was before «URL exacta»
  function current() {
    if (limCb && limCb.checked && lim && lim.value === '1') return 'exact';
    if (sub && sub.checked) return 'all';
    if (out && !out.checked) return 'folder';
    return 'subdomain';
  }
  function apply(v) {
    if (v === 'exact') {
      if (!before) before = {on: limCb.checked, val: lim.value};
      limCb.checked = true; lim.disabled = false; lim.value = '1';
      return;
    }
    if (before || current() === 'exact') {
      limCb.checked = before ? before.on : false;
      lim.value = before && before.val !== '1' ? before.val : '10000';
      lim.disabled = !limCb.checked; before = null;
    }
    if (sub) sub.checked = v === 'all';
    if (out) out.checked = v !== 'folder';
  }
  sel.value = current();
  sel.addEventListener('change', function () { apply(sel.value); });
  window.applyScope = function () { apply(sel.value); };  // the start-page cards reset the options and call this
  document.getElementById('cfg').addEventListener('close', function () { sel.value = current(); });
})();
