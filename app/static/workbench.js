/* Overview chart: clicking a group (Internos, Códigos de respuesta, H1…) in «Resumen» draws its filters. */
(function () {
  function draw(group) {
    var box = document.getElementById('side-chart');
    if (!box || !group) return;
    var title = (group.querySelector(':scope > summary .lbl-t') || group.querySelector(':scope > summary .lbl') || {}).textContent || '';
    var rows = Array.from(group.querySelectorAll(':scope > a.tr, :scope > div.tr')).map(function (r) {
      var l = r.querySelector('.lbl'); return {label: l && l.firstChild ? l.firstChild.textContent.trim() : '', n: parseInt((r.querySelector('.num') || {}).textContent, 10) || 0};
    }).filter(function (r) { return r.label !== 'Todo'; });
    if (!rows.length) return;
    var max = Math.max.apply(null, rows.map(function (r) { return r.n; }).concat([1]));
    var h = '<div class="chart-title">' + title.replace(/</g, '&lt;') + '</div><div class="chart-bars">';
    rows.forEach(function (r) {
      h += '<div class="cb-row" title="' + r.label.replace(/"/g, '&quot;') + ': ' + r.n + '"><span class="cb-label">' +
           r.label.replace(/</g, '&lt;') + '</span><span class="cb-bar"><span style="width:' + (100 * r.n / max).toFixed(1) +
           '%"></span></span><span class="cb-n">' + r.n + '</span></div>';
    });
    box.innerHTML = h + '</div>';
  }
  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('#side details.tg[data-tab] > summary, #side details.tg[data-tab] > .tr');
    if (t) draw(t.parentElement);
  });
})();

/* Beginner mode: only the essential tabs (URLs, response codes, titles, H1, problems). Off by default; a per-browser preference. */
var SIMPLE_KEY = 'letscrawl.simple';
function isSimple() { try { return localStorage.getItem(SIMPLE_KEY) === '1'; } catch (e) { return false; } }  // off until the user turns it on
function setSimple(on) {
  try { localStorage.setItem(SIMPLE_KEY, on ? '1' : '0'); } catch (e) {}
  syncSimple();
  document.dispatchEvent(new Event('letscrawl:simple'));
  document.querySelectorAll('details.mb[open]').forEach(function (d) { d.open = false; });
}
function syncSimple() {
  var on = isSimple();
  document.body.classList.toggle('simple', on);
  document.querySelectorAll('.simple-toggle').forEach(function (c) { c.checked = on; });
}
document.addEventListener('DOMContentLoaded', syncSimple);
document.addEventListener('htmx:afterSwap', syncSimple);

/* [?] help: one floating box that explains why a problem matters for Google and how to fix it. */
(function () {
  var tip = null, owner = null;
  function esc(t) { return String(t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
  function show(el) {
    if (!tip) { tip = document.createElement('div'); tip.className = 'qtip'; tip.setAttribute('role', 'tooltip'); document.body.appendChild(tip); }
    owner = el;
    var h1 = el.dataset.h1 || lcT('Por qué importa'), h2 = el.dataset.h2 || lcT('Cómo se arregla');
    tip.innerHTML = '<b>' + esc(h1) + '</b><p>' + esc(el.dataset.why) + '</p><b>' + esc(h2) + '</b><p>' + esc(el.dataset.fix) + '</p>';
    tip.style.display = 'block';
    var r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    var x = Math.min(Math.max(8, r.left - w / 2 + r.width / 2), innerWidth - w - 8);
    var y = r.bottom + 6 + h > innerHeight ? r.top - h - 6 : r.bottom + 6;
    tip.style.left = x + 'px'; tip.style.top = Math.max(8, y) + 'px';
  }
  function hide() { if (tip) tip.style.display = 'none'; owner = null; }
  document.addEventListener('mouseover', function (e) { var q = e.target.closest && e.target.closest('.qhelp'); if (q) show(q); else if (owner) hide(); });
  document.addEventListener('focusin', function (e) { if (e.target.classList && e.target.classList.contains('qhelp')) show(e.target); });
  document.addEventListener('focusout', function (e) { if (e.target === owner) hide(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
  // a click on [?] explains, it does not open the filter behind it
  document.addEventListener('click', function (e) {
    var q = e.target.closest && e.target.closest('.qhelp');
    if (q) { e.preventDefault(); e.stopPropagation(); show(q); }
  }, true);
  document.addEventListener('htmx:beforeSwap', hide);
})();

// Ctrl+C / Cmd+C copies the selected grid row (tab-separated, so it pastes into Excel in columns),
// unless the user has selected some text themselves or is typing in a field.
(function () {
  function rowText(tr) {
    return Array.prototype.map.call(tr.children, function (td, i) {
      if (i === 0 && td.classList.contains('n')) return null;  // the row number is not data
      return (td.textContent || '').replace(/\s+/g, ' ').trim();
    }).filter(function (v) { return v !== null; }).join('\t');
  }
  function flash(msg) {
    document.querySelectorAll('.statusline').forEach(function (s) {
      var old = s.querySelector('.copied'); if (old) old.remove();
      var b = document.createElement('span'); b.className = 'copied'; b.textContent = ' · ' + msg;
      s.appendChild(b); setTimeout(function () { b.remove(); }, 1600);
    });
  }
  // the detail panel (Nombre / Valor): a click selects a field; the last table clicked decides what Ctrl+C copies
  var lastPick = 'grid';
  document.addEventListener('click', function (e) {
    if (!e.target.closest) return;
    var tr = e.target.closest('table.kv-pick tr');
    if (tr) {
      document.querySelectorAll('table.kv-pick tr.kv-sel').forEach(function (x) { x.classList.remove('kv-sel'); });
      tr.classList.add('kv-sel'); lastPick = 'kv';
    } else if (e.target.closest('table.dg tbody tr')) { lastPick = 'grid'; }
  });
  document.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey || (e.key || '').toLowerCase() !== 'c') return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    var s = window.getSelection && window.getSelection();
    if (s && String(s).trim()) return;  // normal copy of the text the user highlighted
    var en = document.documentElement.lang === 'en', text, msg;
    var kv = lastPick === 'kv' && document.querySelector('table.kv-pick tr.kv-sel');
    if (kv) {  // a field of the detail panel: copy just its value
      text = (kv.querySelector('td').textContent || '').replace(/\s+/g, ' ').trim();
      msg = en ? 'Value copied' : 'Valor copiado';
    } else {
      var trs = document.querySelectorAll('table.dg tbody tr.sel');
      if (!trs.length) return;
      text = Array.prototype.map.call(trs, rowText).join('\n');
      msg = en ? 'Row copied' : 'Fila copiada';
    }
    e.preventDefault();
    var done = function () { flash(msg); };
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); }); }
    else { fallback(text); done(); }
  });
  function fallback(text) {
    var ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', '');
    ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (x) {} ta.remove();
  }
})();

// «Configurar la IA» dialog (in the menu bar partial, so it exists on every workbench page)
function openAiSetup() {
  var d = document.getElementById('ai-setup-dlg'); if (!d) return;
  if (!d.open) d.showModal();
  htmx.ajax('GET', '/ai/setup', {target: '#ai-setup-slot', swap: 'innerHTML'});
}
// «Consultor SEO»: a chat with the configured AI. The conversation is kept by the server (one per audit).
var consultCrawl = 0, consultUrl = '';
function openConsult(crawlId, url) {
  var d = document.getElementById('consult-dlg'); if (!d) return;
  consultCrawl = crawlId || 0;
  openDlg('consult-dlg');
  htmx.ajax('GET', '/consultor?crawl=' + consultCrawl + (url ? '&url=' + encodeURIComponent(url) : ''), {target: '#consult-slot', swap: 'innerHTML'});
}
// the round button in the corner: opens the consultant about the URL picked last in the table (never opens by itself)
function openConsultHere(crawlId) { openConsult(crawlId, crawlId ? consultUrl : ''); }
(function () {
  var KEY = 'letscrawl.nofab';
  function hidden() { try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; } }
  function sync() { document.body.classList.toggle('no-fab', hidden()); }
  window.toggleConsultFab = function () {
    try { if (hidden()) localStorage.removeItem(KEY); else localStorage.setItem(KEY, '1'); } catch (e) {}
    sync(); document.querySelectorAll('details.mb[open]').forEach(function (m) { m.open = false; });
  };
  document.addEventListener('DOMContentLoaded', sync);
  document.addEventListener('click', function (e) {
    var tr = e.target.closest && e.target.closest('table.dg tbody tr[data-url]');
    if (!tr) return;
    consultUrl = tr.dataset.url;
    var fab = document.getElementById('consult-fab'); if (!fab) return;
    fab.classList.add('has-url');
    fab.title = (document.documentElement.lang === 'en' ? 'Ask the AI consultant about ' : 'Preguntar al consultor con IA por ') + consultUrl;
  });
})();
// after «Configura tu API» saves a provider, the open chat picks it up at once
document.addEventListener('aiChanged', function () {   // htmx fires it on the setup form; it bubbles up here
  var d = document.getElementById('consult-dlg');
  if (d && d.open) htmx.ajax('GET', '/consultor?crawl=' + consultCrawl, {target: '#consult-slot', swap: 'innerHTML'});
});
(function () {
  function scrollLog() { var l = document.getElementById('consult-log'); if (l) l.scrollTop = l.scrollHeight; }
  document.addEventListener('htmx:afterSettle', function (e) { if (e.target.closest && e.target.closest('#consult-dlg')) scrollLog(); });
  // a suggested question sends its own (possibly translated) text
  document.addEventListener('htmx:configRequest', function (e) {
    var el = e.detail.elt; if (el && el.classList && el.classList.contains('consult-s')) e.detail.parameters.q = el.textContent.trim();
  });
  // show the question at once (the answer can take a few seconds) and empty the box
  document.addEventListener('htmx:beforeRequest', function (e) {
    var el = e.detail.elt; if (!el || !el.closest || !el.closest('#consult-box')) return;
    var q = el.id === 'consult-form' ? el.querySelector('textarea').value : (el.classList.contains('consult-s') ? el.textContent.trim() : '');
    var log = document.getElementById('consult-log'), wait = log && log.querySelector('.consult-wait');
    if (q && log) {
      var m = document.createElement('div'); m.className = 'cmsg user'; var p = document.createElement('p'); p.textContent = q;
      m.appendChild(p); log.insertBefore(m, wait); scrollLog();
    }
    var ta = document.querySelector('#consult-form textarea'); if (ta && el.id === 'consult-form') ta.value = '';
  });
  // Intro sends, Shift+Intro makes a new line
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    var ta = e.target; if (!ta.closest || !ta.closest('#consult-form') || ta.tagName !== 'TEXTAREA') return;
    e.preventDefault(); if (ta.value.trim()) ta.form.requestSubmit();
  });
})();
function aiPick(radio) {
  var box = radio.closest('.ai-setup');
  box.querySelectorAll('.ai-fields').forEach(function (f) { f.hidden = f.dataset.prov !== radio.value; });
}
function aiTask(radio) {
  var w = document.getElementById('bulk-prompt-wrap'); if (!w) return;
  w.hidden = radio.value !== 'custom';
  if (!w.hidden) document.getElementById('bulk-prompt').focus();
}

// The grid refreshes itself every few seconds during a crawl; skip a refresh while the user is typing in a field
// (done here instead of an htmx trigger filter, so the page works with eval disabled).
// Infinite scroll: when the table refreshes itself during a crawl, ask for as many rows as are already loaded
// and keep the scroll position, so the list does not jump back to the top every few seconds.
document.addEventListener('htmx:configRequest', function (e) {
  var el = e.detail.elt;
  if (!el || el.id !== 'grid' || e.detail.verb !== 'get' || e.detail.target !== el) return;
  var n = el.querySelectorAll('table.dg tbody tr[data-url]').length;
  if (n) e.detail.parameters.upto = n;
});
var gridScroll = null;
document.addEventListener('htmx:beforeSwap', function (e) {
  var g = e.detail.target;
  gridScroll = null;
  if (g && g.id === 'grid' && e.detail.requestConfig && e.detail.requestConfig.elt === g) {
    var sc = g.querySelector('.grid-scroll, .dg-wrap') || (g.querySelector('table.dg') || {}).parentElement;
    if (sc) gridScroll = {top: sc.scrollTop, left: sc.scrollLeft, src: g.dataset.src};
  }
});
document.addEventListener('htmx:afterSettle', function () {
  if (!gridScroll) return;
  var g = document.getElementById('grid'), keep = gridScroll; gridScroll = null;
  if (!g || g.dataset.src !== keep.src) return;
  var sc = g.querySelector('.grid-scroll, .dg-wrap') || (g.querySelector('table.dg') || {}).parentElement;
  if (sc) { sc.scrollTop = keep.top; sc.scrollLeft = keep.left; }
});
document.addEventListener('htmx:beforeRequest', function (e) {
  var el = e.detail && e.detail.elt, a = document.activeElement;
  if (el && el.id === 'grid' && a && a.tagName === 'INPUT') e.preventDefault();
});

// SERP preview in the detail panel (Detalles › SERP): pixel widths measured like Google's truncation.
  (function () {
    var c = document.createElement('canvas').getContext('2d');
    function width(text, font) { c.font = font; return c.measureText(text).width; }
    function cut(text, font, max) {
      if (width(text, font) <= max) return [text, false];
      while (text.length && width(text + ' …', font) > max) text = text.slice(0, -1);
      return [text.replace(/\s+\S*$/, '') + ' …', true];
    }
    window.serpUpdate = function () {
      var mobile = document.querySelector('input[name=serp_dev]:checked').value === 'mobile';
      var t = document.getElementById('serp_title').value, d = document.getElementById('serp_desc').value;
      var tw = mobile ? 500 : 600, dw = mobile ? 680 : 920;   // description wraps on ~2 lines
      var tf = '20px Arial', df = '14px Arial';
      var ct = cut(t || '(sin título)', tf, tw), cd = cut(d || '(Google generará un fragmento)', df, dw);
      document.getElementById('serp_t').textContent = ct[0];
      document.getElementById('serp_d').textContent = cd[0];
      document.getElementById('serp_box').style.maxWidth = (mobile ? 500 : 600) + 'px';
      document.getElementById('serp_info').textContent = 'Título: ' + t.length + ' caracteres · ' + Math.round(width(t, tf)) + ' px' +
        (ct[1] ? ' (se truncará)' : '') + ' — Descripción: ' + d.length + ' caracteres · ' + Math.round(width(d, df)) + ' px' + (cd[1] ? ' (se truncará)' : '');
    };
  })();
  document.addEventListener('htmx:afterSwap', function () { if (document.getElementById('serp_title')) serpUpdate(); });
document.addEventListener('DOMContentLoaded', function () { if (document.getElementById('serp_title')) serpUpdate(); });

// «?» next to every option: the explanations come from #opt-help (rendered and translated by the server) and are
// placed next to the label of each field that has one. Runs again after htmx swaps (dialogs loaded later).
function attachOptionHelp() {
  var src = document.getElementById('opt-help');
  if (!src) return;
  src.querySelectorAll('span[data-key]').forEach(function (h) {
    document.querySelectorAll('[name="' + h.dataset.key + '"]').forEach(function (ctl) {
      if (ctl.type === 'hidden' || ctl.closest('#opt-help')) return;
      var spot = null, after = false, row;
      if ((row = ctl.closest('.rs-row'))) spot = row.querySelector('.rs-label');
      else if (ctl.closest('label')) { var lab = ctl.closest('label'); spot = lab.querySelector('span') || lab; }
      else if ((row = ctl.closest('.lim-row, .mm-row'))) spot = row.querySelector('label.check > span, :scope > span');
      else if (ctl.id && document.querySelector('label[for="' + ctl.id + '"]')) spot = document.querySelector('label[for="' + ctl.id + '"]');
      else if (ctl.closest('.field') && ctl.closest('.field').querySelector('label')) spot = ctl.closest('.field').querySelector('label');
      else if (ctl.tagName !== 'TEXTAREA') { spot = ctl; after = true; }
      else {  // big text boxes: next to the title of their box, or the description of their section
        var fs = ctl.closest('fieldset'), sec = ctl.closest('section');
        spot = (fs && fs.querySelector('legend')) || (sec && sec.querySelector('.desc'));
      }
      if (!spot) return;
      var holder = after ? spot.parentNode : spot;
      if (after ? (spot.nextElementSibling && spot.nextElementSibling.classList.contains('qhelp')) : spot.querySelector('.qhelp')) return;
      var q = document.createElement('span');
      q.className = 'qhelp'; q.tabIndex = 0; q.setAttribute('role', 'button');
      q.setAttribute('aria-label', h.getAttribute('aria-label'));
      ['why', 'fix', 'h1', 'h2'].forEach(function (k) { q.dataset[k] = h.dataset[k]; });
      q.textContent = '?';
      if (after) holder.insertBefore(q, spot.nextSibling); else { spot.appendChild(document.createTextNode(' ')); spot.appendChild(q); }
    });
  });
  // rows without a field of their own (always-on options, section descriptions…) say which help they use
  document.querySelectorAll('[data-help]').forEach(function (spot) {
    if (spot.querySelector('.qhelp')) return;
    var h = src.querySelector('span[data-key="' + spot.dataset.help + '"]');
    if (!h) return;
    var q = document.createElement('span');
    q.className = 'qhelp'; q.tabIndex = 0; q.setAttribute('role', 'button');
    q.setAttribute('aria-label', h.getAttribute('aria-label'));
    ['why', 'fix', 'h1', 'h2'].forEach(function (k) { q.dataset[k] = h.dataset[k]; });
    q.textContent = '?';
    spot.appendChild(document.createTextNode(' ')); spot.appendChild(q);
  });
}
document.addEventListener('DOMContentLoaded', attachOptionHelp);
document.addEventListener('htmx:afterSwap', attachOptionHelp);

// Tab bars wider than their panel: the mouse wheel scrolls them sideways, and the open tab is kept in view.
document.addEventListener('wheel', function (e) {
  var bar = e.target.closest && e.target.closest('.tabbar');
  if (!bar || bar.scrollWidth <= bar.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  bar.scrollLeft += e.deltaY; e.preventDefault();
}, {passive: false});
function showActiveTabs(root) {
  (root || document).querySelectorAll('.tabbar a.on').forEach(function (a) {
    var bar = a.parentElement, b = bar.getBoundingClientRect(), r = a.getBoundingClientRect();
    if (r.right > b.right) bar.scrollLeft += r.right - b.right + 40;  // only when the open tab is hidden
    else if (r.left < b.left) bar.scrollLeft -= b.left - r.left + 40;
  });
}
document.addEventListener('DOMContentLoaded', function () { showActiveTabs(); });
document.addEventListener('htmx:afterSwap', function (e) { showActiveTabs(e.target); });
