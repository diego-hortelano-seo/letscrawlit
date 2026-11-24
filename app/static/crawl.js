/* Audit page (crawl.html): AI in bulk, notes per URL, row selection and the tab chooser. */
var CRAWL_ID = pageData().crawlId;
document.body.addEventListener('aiChanged', function () {
  var d = document.getElementById('ai-dlg');
  if (d && d.open && !document.getElementById('ai-setup-dlg').open) setTimeout(openBulk, 1200);
});
document.getElementById('ai-setup-dlg').addEventListener('close', function () {
  if (document.getElementById('ai-dlg').open) openBulk();
});
var BULK_TASK = '';
function openBulk(task) {  // task: the card to show already chosen (menu «IA», the button in a URL's AI panel)
  if (typeof task === 'string') BULK_TASK = task;
  var st = document.getElementById('grid-state'), qs = new URLSearchParams();
  if (st) st.querySelectorAll('input').forEach(function (i) { qs.set(i.name, i.value); });
  if (BULK_TASK) qs.set('task', BULK_TASK);
  if (SEL_URLS.length) qs.set('nsel', SEL_URLS.length);  // offer «Solo las URLs que has seleccionado»
  var d = document.getElementById('ai-dlg');
  if (!d.open) d.showModal();
  htmx.ajax('GET', '/crawls/' + CRAWL_ID + '/ai/bulk?' + qs.toString(), {target: '#ai-bulk-slot', swap: 'innerHTML'});
}
(function () {  // /crawls/<id>?ai=seo_pack (from a URL's AI panel opened on its own page): open «IA en bloque» at once
  var p = new URLSearchParams(location.search), t = p.get('ai');
  if (!t) return;
  p.delete('ai'); history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p : ''));
  document.addEventListener('DOMContentLoaded', function () { openBulk(t); });
  if (document.readyState !== 'loading') openBulk(t);
})();
// --- notes per URL: right click on a row, or the link in the detail panel -------------------------
var NOTE_ENDPOINT = '/crawls/' + CRAWL_ID + '/note';
var ctxUrl = null;
function hideCtx() { document.getElementById('ctx').hidden = true; }
document.addEventListener('contextmenu', function (e) {
  var tr = e.target.closest && e.target.closest('#grid table.dg tbody tr[data-url]');
  if (!tr) { hideCtx(); return; }
  e.preventDefault();
  ctxUrl = tr.dataset.url;
  selRow(tr, 'keep');
  var m = document.getElementById('ctx');
  m.hidden = false;
  var x = Math.min(e.clientX, window.innerWidth - m.offsetWidth - 8), y = Math.min(e.clientY, window.innerHeight - m.offsetHeight - 8);
  m.style.left = x + 'px'; m.style.top = y + 'px';
  m.querySelector('button:not([hidden])').focus();
});
document.addEventListener('click', function (e) { if (!e.target.closest('#ctx')) hideCtx(); });
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hideCtx(); });
document.addEventListener('scroll', hideCtx, true);
document.getElementById('ctx').addEventListener('click', function (e) {
  var b = e.target.closest('button'); if (!b || !ctxUrl) return;
  hideCtx();
  if (b.dataset.act === 'apuntar') journalForUrl(ctxUrl);
  else if (b.dataset.act === 'note') openNote(ctxUrl);
  else if (b.dataset.act === 'open') window.open(ctxUrl, '_blank', 'noopener');
  else if (b.dataset.act === 'copy' && navigator.clipboard) navigator.clipboard.writeText(ctxUrl);
});
function openNote(url) {
  var f = document.getElementById('note-form'), err = document.getElementById('note-err');
  err.hidden = true;
  fetch(NOTE_ENDPOINT + '?url=' + encodeURIComponent(url)).then(function (r) { return r.json(); }).then(function (n) {
    f.url.value = url; f.note.value = n.note || ''; f.status.value = n.status || 'pendiente';
    document.getElementById('note-url').textContent = url;
    document.getElementById('note-dlg').showModal();
    f.note.focus();
  });
}
document.getElementById('note-form').addEventListener('submit', function (e) {
  e.preventDefault();
  var f = e.target, err = document.getElementById('note-err');
  fetch(NOTE_ENDPOINT, {method: 'POST', body: new FormData(f)}).then(function (r) {
    if (!r.ok) return r.text().then(function (t) { throw new Error(t || r.statusText); });
    document.getElementById('note-dlg').close();
    var g = document.getElementById('grid');
    if (g && window.htmx) htmx.ajax('GET', g.dataset.src, {target: '#grid', swap: 'outerHTML'});
    var sel = document.querySelector('table.dg tr.sel');
    if (sel && sel.dataset.url === f.url.value && window.htmx) htmx.trigger(sel, 'click');
  }).catch(function (x) { err.textContent = lcT('No se pudo guardar: ') + x.message; err.hidden = false; });
});

// Row selection. One click selects one URL. Several: the «Seleccionar varias» button (each click adds or
// removes a row), Ctrl+click (add/remove) or Shift+click (a whole range). The selection survives the
// table refreshes while a crawl is running.
var SEL_URLS = [], SEL_ANCHOR = null, MULTI = false;
function gridRows() { return Array.prototype.slice.call(document.querySelectorAll('#grid table.dg tbody tr[data-url]')); }
function selRow(tr, ev) {
  var url = tr.dataset.url;
  if (!url) return;
  var add = MULTI || (ev && (ev.ctrlKey || ev.metaKey));
  if (ev && ev.shiftKey && SEL_ANCHOR) {
    var rows = gridRows(), a = rows.findIndex(function (r) { return r.dataset.url === SEL_ANCHOR; }), b = rows.indexOf(tr);
    if (a >= 0 && b >= 0) {
      if (!add) SEL_URLS = [];
      rows.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(function (r) { if (SEL_URLS.indexOf(r.dataset.url) < 0) SEL_URLS.push(r.dataset.url); });
    }
  } else if (add) {
    var k = SEL_URLS.indexOf(url);
    if (k >= 0) SEL_URLS.splice(k, 1); else SEL_URLS.push(url);
    SEL_ANCHOR = url;
  } else if (!(ev === 'keep' && SEL_URLS.indexOf(url) >= 0)) {
    SEL_URLS = [url]; SEL_ANCHOR = url;
  }
  paintSelection();
}
function clearSelection() { SEL_URLS = []; SEL_ANCHOR = null; paintSelection(); }
function paintSelection() {
  gridRows().forEach(function (r) { r.classList.toggle('sel', SEL_URLS.indexOf(r.dataset.url) >= 0); });
  var n = SEL_URLS.length;
  document.querySelectorAll('.sel-count').forEach(function (s) {
    s.textContent = !n ? lcT('Ninguna URL seleccionada') : n === 1 ? lcT('1 URL seleccionada') : n + lcT(' URLs seleccionadas');
  });
  var c = document.getElementById('copy-sel-btn');
  if (c) { c.hidden = n < 2; c.textContent = lcT('Copiar URLs') + ' (' + n + ')'; }
  var m = document.getElementById('multi-btn');
  if (m) { m.setAttribute('aria-pressed', MULTI ? 'true' : 'false'); m.classList.toggle('on', MULTI); }
  if (typeof syncApuntarBtn === 'function') syncApuntarBtn();
}
function toggleMulti() { MULTI = !MULTI; paintSelection(); }
function copySelected() {
  var text = SEL_URLS.join('\n');
  var done = function () {
    document.querySelectorAll('.statusline').forEach(function (s) {
      var b = document.createElement('span'); b.className = 'copied'; b.textContent = ' · ' + lcT('URLs copiadas');
      s.appendChild(b); setTimeout(function () { b.remove(); }, 1600);
    });
  };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
  else { var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); done(); }
}
// Shift+click must not highlight the page text
document.addEventListener('mousedown', function (e) {
  if (e.shiftKey && e.target.closest && e.target.closest('#grid table.dg tbody tr[data-url]')) e.preventDefault();
});
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape' || SEL_URLS.length < 2 || document.querySelector('dialog[open]')) return;
  var t = e.target;  // Esc that closes a dropdown or leaves a field must not drop the selection
  if (t && (t.closest('#journal') || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
  clearSelection();
});
document.addEventListener('htmx:afterSwap', paintSelection);
// «IA en bloque» › «Solo las URLs que has seleccionado»: the form carries the rows ticked in the table
document.addEventListener('htmx:afterSettle', function () {
  var h = document.getElementById('bulk-sel-urls');
  if (h) h.value = SEL_URLS.join(String.fromCharCode(10));
});
// Tab chooser ("Configurar pestañas"): which tabs are visible is a per-browser preference.
var TABS_KEY = 'letscrawl.hiddenTabs';
function hiddenTabs() { try { return JSON.parse(localStorage.getItem(TABS_KEY) || '[]'); } catch (e) { return []; } }
var SIMPLE_TABS = pageData().simpleTabs || [];
function applyTabs() {
  var h = hiddenTabs(), simple = isSimple();
  document.querySelectorAll('[data-tab]').forEach(function (el) {
    var k = el.dataset.tab, off = h.indexOf(k) >= 0 || (simple && SIMPLE_TABS.indexOf(k) < 0);
    if (el.matches('input')) { el.checked = h.indexOf(k) < 0; el.disabled = simple; }
    else { el.hidden = off; }
  });
}
document.addEventListener('letscrawl:simple', applyTabs);
function toggleTab(input) {
  var h = hiddenTabs().filter(function (k) { return k !== input.dataset.tab; });
  if (!input.checked) h.push(input.dataset.tab);
  try { localStorage.setItem(TABS_KEY, JSON.stringify(h)); } catch (e) {}
  applyTabs();
}
function resetTabs() { try { localStorage.removeItem(TABS_KEY); } catch (e) {} applyTabs(); }
applyTabs();
document.addEventListener('htmx:afterSwap', applyTabs);

// --- «Apuntes» drawer: notebook of the site, next to the table (not modal, so you can keep working) --------
function toggleJournal() {
  var j = document.getElementById('journal');
  if (!j) return;
  j.hidden = !j.hidden;
  document.querySelectorAll('details.mb[open]').forEach(function (m) { m.open = false; });
  if (!j.hidden) htmx.ajax('GET', '/crawls/' + CRAWL_ID + '/journal', {target: '#journal-slot', swap: 'innerHTML'});
}
// «Apuntar»: a little window to write a note about the URLs selected in the table. The chips add the
// problems those URLs have (or common phrases) to the text with one click.
function journalCompose(urls) {
  if (!urls.length) return;
  var d = document.getElementById('apuntar-dlg'), slot = document.getElementById('apuntar-slot');
  slot.innerHTML = '<span class="spinner"></span>';
  d.showModal();
  htmx.ajax('POST', '/crawls/' + CRAWL_ID + '/journal/compose', {target: '#apuntar-slot', swap: 'innerHTML',
    values: {urls: urls.join(String.fromCharCode(10))}}).then(function () {
    var t = document.getElementById('ap-text');
    if (t) t.focus();
  });
}
function journalForUrl(url) {  // from the detail panel or the right-click menu: that one URL
  if (SEL_URLS.length !== 1 || SEL_URLS[0] !== url) { SEL_URLS = [url]; SEL_ANCHOR = url; paintSelection(); }
  journalCompose([url]);
}
function journalForSelected() { journalCompose(SEL_URLS.slice()); }
function copyBlock(btn) {  // AI proposals: copy the value next to the button (title, description or the JSON-LD <script>)
  var v = btn.closest('.sp-field').querySelector('.sp-val'), text = v ? v.textContent.trim() : '';
  var done = function () { var o = btn.textContent; btn.textContent = lcT('Copiado'); btn.classList.add('ok'); setTimeout(function () { btn.textContent = o; btn.classList.remove('ok'); }, 1400); };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
  else { var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); done(); }
}
function apuntarAdd(text) {  // a chip: add its line to the note; a second click takes it out again
  var t = document.getElementById('ap-text');
  if (!t) return;
  var NL = String.fromCharCode(10), line = '• ' + text, cur = t.value.replace(/\s+$/, '');
  var lines = cur ? cur.split(NL) : [], k = lines.indexOf(line), on = k < 0;
  if (on) { if (cur.indexOf(text) < 0) lines.push(line); else on = true; } else lines.splice(k, 1);
  t.value = lines.length ? lines.join(NL) + NL : '';
  if (this && this.classList) { this.classList.toggle('on', on); this.setAttribute('aria-pressed', on ? 'true' : 'false'); }
  t.focus(); t.setSelectionRange(t.value.length, t.value.length);
}
// Saved: close the window, show the on the rows and the new count, without moving the table
document.addEventListener('htmx:afterRequest', function (e) {
  if (!e.detail.elt || e.detail.elt.id !== 'apuntar-form' || !e.detail.successful) return;
  document.getElementById('apuntar-dlg').close();
  refreshGrid();
  if (SEL_URLS.length === 1) htmx.ajax('GET', '/crawls/' + CRAWL_ID + '/detail?src=pages&url=' + encodeURIComponent(SEL_URLS[0]), {target: '#detail', swap: 'innerHTML'});
  var j = document.getElementById('journal');
  if (j && !j.hidden) htmx.ajax('GET', '/crawls/' + CRAWL_ID + '/journal', {target: '#journal-slot', swap: 'innerHTML'});
  document.querySelectorAll('.statusline').forEach(function (s) {
    var b = document.createElement('span'); b.className = 'copied'; b.textContent = ' · ' + lcT('Nota guardada');
    s.appendChild(b); setTimeout(function () { b.remove(); }, 2000);
  });
});
// «IA en bloque» started (or stopped): the «IA» tab shows the progress and the new columns as they arrive
document.addEventListener('htmx:afterRequest', function (e) {
  var p = e.detail.pathInfo && e.detail.pathInfo.requestPath || '';
  if (e.detail.successful && /\/ai\/bulk(\/cancel)?$/.test(p) && e.detail.requestConfig && e.detail.requestConfig.verb === 'post'
      && document.querySelector('#grid .ai-banner')) refreshGrid();
});
function refreshGrid() {
  var g = document.getElementById('grid');
  if (!g) return;
  var sc = g.querySelector('.grid-scroll'), top = sc ? sc.scrollTop : 0, n = gridRows().length;
  htmx.ajax('GET', g.dataset.src + '&upto=' + n, {target: '#grid', swap: 'outerHTML'}).then(function () {
    var s2 = document.querySelector('#grid .grid-scroll');
    if (s2) s2.scrollTop = top;
  });
}
// «Exportar resumen» and the PDF reports: ask for an optional comment for the client and whether to add the notes
function openDownload(summary) {
  var d = document.getElementById('download-dlg'), btn = document.getElementById('jn-btn');
  var n = btn ? +btn.dataset.total || 0 : 0;
  document.getElementById('dl-summary').value = summary ? '1' : '0';
  document.getElementById('dl-title').textContent = summary ? lcT('Descargar el resumen en PDF') : lcT('Descargar el informe completo en PDF');
  document.getElementById('dl-notes-n').textContent = n;
  document.getElementById('dl-notes-row').hidden = !n;
  document.getElementById('dl-notes').checked = n > 0;
  document.querySelectorAll('details.mb[open]').forEach(function (m) { m.open = false; });
  d.showModal();
  document.getElementById('dl-comment').focus();
  return false;
}
function downloadGo() { setTimeout(function () { document.getElementById('download-dlg').close(); }, 300); }
// «Editar» / «Cancelar» on an item: swap the text for its edit form
function journalEdit(el) {
  var item = el.closest('.jn-item'), f = item.querySelector('.jn-editform'), m = item.querySelector('details.jn-more');
  if (m) m.open = false;
  f.hidden = !f.hidden;
  item.querySelector('.jn-text').hidden = !f.hidden;
  if (!f.hidden) { var ta = f.querySelector('textarea'); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}
function syncApuntarBtn() {  // the toolbar «Apuntar» shows only while a URL row is selected
  var b = document.getElementById('jn-url-btn');
  if (b) b.hidden = !SEL_URLS.length;
}
document.addEventListener('click', function () { setTimeout(syncApuntarBtn, 0); });
document.addEventListener('htmx:afterSwap', syncApuntarBtn);
function journalGoUrl(url) {
  var row = Array.prototype.find.call(document.querySelectorAll('table.dg tbody tr[data-url]'), function (r) { return r.dataset.url === url; });
  if (row) { var m = MULTI; MULTI = false; row.click(); MULTI = m; row.scrollIntoView({block: 'center'}); return; }
  htmx.ajax('GET', '/crawls/' + CRAWL_ID + '/detail?src=pages&url=' + encodeURIComponent(url), {target: '#detail', swap: 'innerHTML'});
}
function journalGoIssue(code) {
  htmx.ajax('GET', '/crawls/' + CRAWL_ID + '/grid?tab=issues&f=' + encodeURIComponent(code), {target: '#grid', swap: 'outerHTML'});
}
document.addEventListener('htmx:afterSwap', function (e) {
  if (!document.getElementById('journal-box')) return;
  var box = document.getElementById('journal-box'), btn = document.getElementById('jn-btn');
  if (btn) btn.dataset.total = box.dataset.total;
  if (btn) btn.textContent = lcT('Notas') + (+box.dataset.total ? ' (' + box.dataset.total + ')' : '');
});
