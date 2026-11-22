/* Colour themes chosen by the user (in the header): previewed live, kept only after «Guardar».
   Saved in this browser (localStorage). */
(function () {
  var KEY = 'letscrawl.theme';
  var PRESETS = {
    letscrawl: {label: 'LetsCrawlit', main: '#331942', accent: '#c77dff', second: '#ff8bd1', builtin: true},
    oceano: {label: 'Océano', main: '#0f2a44', accent: '#38bdf8', second: '#fbbf24'},
    bosque: {label: 'Bosque', main: '#14321f', accent: '#4ade80', second: '#facc15'},
    atardecer: {label: 'Atardecer', main: '#3b1d14', accent: '#fb923c', second: '#f472b6'},
    grafito: {label: 'Grafito', main: '#1f2937', accent: '#60a5fa', second: '#e5e7eb'},
    cereza: {label: 'Cereza', main: '#3f0d1d', accent: '#fb7185', second: '#fde68a'}
  };

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function save(t) {
    try { localStorage.setItem(KEY, JSON.stringify(t)); } catch (e) {}
  }
  function hex(v) { return /^#[0-9a-f]{6}$/i.test(v || '') ? v : null; }
  function readable(c) {  // black or white text, whichever contrasts more (WCAG relative luminance)
    var n = parseInt(c.slice(1), 16), ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    var L = 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
    return (L + 0.05) / 0.05 > 1.05 / (L + 0.05) ? '#111111' : '#ffffff';
  }

  function apply(t) {
    var root = document.documentElement;
    var mode = t.mode || 'dark';  // dark unless the user picks light or «según el sistema» (auto)
    if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
    var main = hex(t.main), accent = hex(t.accent), second = hex(t.second);
    var props = ['--c-main', '--c-accent', '--c-second', '--on-main', '--on-accent', '--on-second'];
    if (main && accent && second && t.preset !== 'letscrawl') {
      root.setAttribute('data-custom', '');
      root.style.setProperty('--c-main', main);
      root.style.setProperty('--c-accent', accent);
      root.style.setProperty('--c-second', second);
      root.style.setProperty('--on-main', readable(main));
      root.style.setProperty('--on-accent', readable(accent));
      root.style.setProperty('--on-second', readable(second));
    } else {
      root.removeAttribute('data-custom');
      props.forEach(function (p) { root.style.removeProperty(p); });
    }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', main && t.preset !== 'letscrawl' ? main : '#331942');
  }

  apply(load());  // runs in <head>, before the page is painted

  function sync(panel, t) {
    panel.querySelectorAll('[data-mode]').forEach(function (b) {
      b.setAttribute('aria-pressed', String((t.mode || 'dark') === b.dataset.mode));
    });
    var preset = t.preset || 'letscrawl';
    panel.querySelectorAll('[data-preset]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(preset === b.dataset.preset));
    });
    var p = PRESETS[preset] || {};
    ['main', 'accent', 'second'].forEach(function (k) {
      var input = panel.querySelector('input[name="' + k + '"]');
      if (input) input.value = hex(t[k]) || p[k] || PRESETS.letscrawl[k];
    });
  }

  function panels() { return document.querySelectorAll('.theme-panel'); }

  // Changes are previewed live but only kept after «Guardar»; «Cancelar», ✕, Esc or clicking
  // outside go back to the saved colours. Both panels on a page share one draft.
  var saved = load(), draft = copy(saved), savedTimer = null;
  function copy(t) { return JSON.parse(JSON.stringify(t || {})); }
  function norm(t) {  // compare what the user sees, not how it was reached
    var p = PRESETS[t.preset || 'letscrawl'] || {};
    return [t.mode || 'dark', t.preset === 'custom' ? 'custom' : (t.preset || 'letscrawl'),
            hex(t.main) || p.main || PRESETS.letscrawl.main, hex(t.accent) || p.accent || PRESETS.letscrawl.accent,
            hex(t.second) || p.second || PRESETS.letscrawl.second].join('|').toLowerCase();
  }
  function dirty() { return norm(draft) !== norm(saved); }

  function refresh(justSaved) {
    var d = dirty();
    panels().forEach(function (panel) {
      sync(panel, draft);
      panel.querySelector('[data-save]').disabled = !d;
      panel.querySelector('.tp-dirty').hidden = !d;
      panel.querySelector('.tp-saved').hidden = !justSaved;
      panel.querySelector('[data-reset]').disabled = norm(draft) === norm({});
    });
    if (justSaved) {
      clearTimeout(savedTimer);
      savedTimer = setTimeout(function () { panels().forEach(function (p) { p.querySelector('.tp-saved').hidden = true; }); }, 2000);
    }
  }
  function preview(t) { draft = t; apply(draft); refresh(false); }
  function revert() {  // also clears «✓ Guardado», so reopening starts clean
    if (dirty()) { draft = copy(saved); apply(draft); }
    clearTimeout(savedTimer); refresh(false);
  }
  function closeHost(panel) {
    var dlg = panel.closest('dialog'), det = panel.closest('details');
    if (dlg && dlg.open) dlg.close();
    if (det) det.open = false;
  }

  function build(panel) {
    var sw = panel.querySelector('.swatches');
    Object.keys(PRESETS).forEach(function (key) {
      var p = PRESETS[key], b = document.createElement('button');
      b.type = 'button'; b.className = 'swatch'; b.dataset.preset = key;
      b.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
      var dots = b.querySelectorAll('i');
      dots[0].style.background = p.main; dots[1].style.background = p.accent; dots[2].style.background = p.second;
      b.appendChild(document.createTextNode(p.label));
      sw.appendChild(b);
    });
    panel.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b || b.disabled) return;
      if (b.dataset.save) {
        save(draft); saved = copy(draft); refresh(true);
        setTimeout(function () { closeHost(panel); }, 600);
        return;
      }
      if (b.dataset.cancel) { revert(); closeHost(panel); return; }
      var t = copy(draft);
      if (b.dataset.mode) t.mode = b.dataset.mode;
      if (b.dataset.preset) {
        var p = PRESETS[b.dataset.preset];
        t.preset = b.dataset.preset; t.main = p.main; t.accent = p.accent; t.second = p.second;
      }
      if (b.dataset.reset) t = {};
      if (b.dataset.mode || b.dataset.preset || b.dataset.reset) preview(t);
    });
    panel.addEventListener('input', function (e) {
      if (e.target.type !== 'color') return;
      var t = copy(draft);
      ['main', 'accent', 'second'].forEach(function (k) { t[k] = panel.querySelector('input[name="' + k + '"]').value; });
      t.preset = 'custom';
      preview(t);
    });
    // leaving without saving restores the saved colours
    var dlg = panel.closest('dialog'), det = panel.closest('details');
    if (dlg) dlg.addEventListener('close', revert);
    if (det) det.addEventListener('toggle', function () { if (!det.open) revert(); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    panels().forEach(build);
    refresh(false);
    var menu = document.querySelector('.theme-menu');
    if (menu) document.addEventListener('click', function (e) { if (menu.open && !menu.contains(e.target)) menu.open = false; });
  });
  // other open tabs follow the change
  window.addEventListener('storage', function (e) {
    if (e.key !== KEY) return;
    var keep = dirty();
    saved = load();
    if (!keep) { draft = copy(saved); apply(draft); }
    refresh(false);
  });
})();
