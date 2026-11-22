/* Visual extractor: pick an element in a script-free copy of the page and turn it into a CSS/XPath extraction. */
(function () {
  var current = null, doc = null;

  function esc(s) { return window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/[^\w-]/g, '\\$&'); }
  function stableClasses(el) {  // skip state/utility classes that change between pages
    return Array.from(el.classList).filter(function (c) {
      return c.length < 40 && !/\d{3,}|active|hover|focus|selected|open|show|hidden|visible|current|js-|is-|has-/i.test(c);
    }).slice(0, 2);
  }
  function part(el, withIndex) {
    var s = el.tagName.toLowerCase();
    var cls = stableClasses(el);
    if (cls.length) s += '.' + cls.map(esc).join('.');
    if (withIndex && el.parentElement) {
      var same = Array.from(el.parentElement.children).filter(function (c) { return c.tagName === el.tagName; });
      if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
    }
    return s;
  }
  function cssFor(el, unique) {
    if (unique && el.id && doc.querySelectorAll('#' + esc(el.id)).length === 1) return '#' + esc(el.id);
    var parts = [], node = el;
    while (node && node.nodeType === 1 && node.tagName !== 'HTML') {
      if (node !== el && node.id && doc.querySelectorAll('#' + esc(node.id)).length === 1) { parts.unshift('#' + esc(node.id)); break; }
      parts.unshift(part(node, unique));
      var sel = parts.join(' > ');
      if (unique && doc.querySelectorAll(sel).length === 1) return sel;
      if (!unique && parts.length >= 3) return sel;
      node = node.parentElement;
    }
    return parts.join(' > ');
  }
  function xpathFor(el) {
    if (el.id) return '//*[@id="' + el.id + '"]';
    var segs = [], node = el;
    while (node && node.nodeType === 1) {
      if (node !== el && node.id) { segs.unshift('//*[@id="' + node.id + '"]'); return segs.join('/'); }
      var same = node.parentNode ? Array.from(node.parentNode.children).filter(function (c) { return c.tagName === node.tagName; }) : [node];
      segs.unshift(node.tagName.toLowerCase() + (same.length > 1 ? '[' + (same.indexOf(node) + 1) + ']' : ''));
      node = node.parentElement;
    }
    return '/' + segs.join('/');
  }
  function valueOf(el, what) {
    if (what === 'text') return (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    return el.getAttribute(what) || '';
  }

  function build() {
    if (!current) return;
    var what = document.getElementById('pick-what').value, scope = document.getElementById('pick-scope').value;
    var css = cssFor(current, scope === 'one'), expr, type, matches;
    if (what === 'text') {
      expr = css; type = 'css';
      matches = Array.from(doc.querySelectorAll(css));
    } else {  // attributes need XPath: //…/@href
      var base = scope === 'one' ? xpathFor(current) : '//' + current.tagName.toLowerCase() +
        (stableClasses(current).length ? '[contains(concat(" ", normalize-space(@class), " "), " ' + stableClasses(current)[0] + ' ")]' : '');
      expr = base + '/@' + what; type = 'xpath';
      var r = doc.evaluate(base, doc, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      matches = []; for (var i = 0; i < r.snapshotLength; i++) matches.push(r.snapshotItem(i));
    }
    document.getElementById('pick-expr').value = expr;
    document.getElementById('pick-expr').dataset.type = type;
    document.getElementById('pick-type').textContent = type === 'css' ? 'Selector CSS' : 'XPath';
    document.getElementById('pick-count').textContent = '(' + matches.length + ' coincidencia' + (matches.length === 1 ? '' : 's') + ' en esta página)';
    var ol = document.getElementById('pick-preview'); ol.innerHTML = '';
    matches.slice(0, 8).forEach(function (m) { var li = document.createElement('li'); li.textContent = valueOf(m, what).slice(0, 140) || '(vacío)'; ol.appendChild(li); });
    doc.querySelectorAll('[data-lc-pick]').forEach(function (n) { n.removeAttribute('data-lc-pick'); });
    matches.forEach(function (m) { m.setAttribute('data-lc-pick', '1'); });
  }

  function choose(el) {
    current = el;
    var what = document.getElementById('pick-what');
    what.innerHTML = '<option value="text">Texto visible</option>';
    ['href', 'src', 'content', 'alt', 'title', 'datetime', 'data-price', 'value'].forEach(function (a) {
      if (el.hasAttribute(a)) what.insertAdjacentHTML('beforeend', '<option value="' + a + '">Atributo ' + a + '</option>');
    });
    document.getElementById('pick-result').hidden = false;
    document.getElementById('pick-help').textContent = 'Elegido: <' + el.tagName.toLowerCase() + '>. Puedes hacer clic en otro elemento para cambiarlo.';
    build();
  }

  window.openPicker = function () {
    var url = (document.getElementById('pick_url').value || (document.getElementById('url') || {}).value || '').trim();
    var dlg = document.getElementById('picker'), frame = document.getElementById('pick-frame');
    document.getElementById('pick-src').textContent = 'Cargando ' + url + '…';
    document.getElementById('pick-result').hidden = true;
    dlg.showModal();
    var body = new FormData(); body.append('pick_url', url);
    fetch('/tools/pick', {method: 'POST', body: body}).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { document.getElementById('pick-src').textContent = d.error; frame.srcdoc = ''; return; }
      document.getElementById('pick-src').textContent = d.url + ' (copia sin scripts)';
      frame.onload = function () {
        doc = frame.contentDocument;
        var st = doc.createElement('style');
        st.textContent = '[data-lc-hover]{outline:2px dashed #c77dff!important;cursor:crosshair!important}' +
                         '[data-lc-pick]{outline:3px solid #ff8bd1!important;background:rgba(255,139,209,.15)!important}';
        doc.head.appendChild(st);
        doc.addEventListener('mouseover', function (e) { if (e.target.setAttribute) e.target.setAttribute('data-lc-hover', '1'); }, true);
        doc.addEventListener('mouseout', function (e) { if (e.target.removeAttribute) e.target.removeAttribute('data-lc-hover'); }, true);
        doc.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); e.target.removeAttribute('data-lc-hover'); choose(e.target); }, true);
        doc.addEventListener('submit', function (e) { e.preventDefault(); }, true);
      };
      frame.srcdoc = d.html;
    }).catch(function () { document.getElementById('pick-src').textContent = 'No se pudo cargar la página.'; });
  };

  document.addEventListener('change', function (e) { if (e.target.id === 'pick-what' || e.target.id === 'pick-scope') build(); });

  window.usePick = function () {
    var expr = document.getElementById('pick-expr'), msg = document.getElementById('pick-msg');
    for (var i = 0; i < 10; i++) {
      var name = document.querySelector('[name="extract_name_' + i + '"]'), ex = document.querySelector('[name="extract_expr_' + i + '"]');
      if (!ex) break;
      if (!ex.value.trim()) {
        name.value = document.getElementById('pick-name').value || 'Dato ' + (i + 1);
        ex.value = expr.value;
        document.querySelector('[name="extract_type_' + i + '"]').value = expr.dataset.type;
        msg.textContent = 'Añadida como extracción ' + (i + 1) + '. Ciérrala y lanza el rastreo.';
        return;
      }
    }
    msg.textContent = 'No quedan huecos libres en Extracción personalizada: borra uno primero.';
  };
})();
