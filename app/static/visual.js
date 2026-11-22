/* Site architecture view (visual.html): force diagram and crawl tree on a canvas. */
(function () {
  var COLORS = {indexable: '#2f9e44', nonindexable: '#868e96', redirect: '#f08c00', error: '#e03131', blocked: '#7048e8'};
  var canvas = document.getElementById('viz'), ctx = canvas.getContext('2d'), tip = document.getElementById('tip');
  var data, mode = 'force', nodes = [], edges = [], scale = 1, ox = 0, oy = 0, drag = null, hover = null, running = 0;
  function fit() { var r = canvas.getBoundingClientRect(); canvas.width = r.width * devicePixelRatio; canvas.height = r.height * devicePixelRatio; }
  window.addEventListener('resize', function () { fit(); draw(); });
  fetch('/crawls/' + pageData().crawlId + '/graph.json').then(function (r) { return r.json(); }).then(function (d) {
    data = d; fit();
    document.getElementById('vinfo').textContent = d.nodes.length + ' páginas · ' + d.edges.length + ' enlaces' + (d.truncated ? ' (se muestran las más importantes)' : '');
    show(new URLSearchParams(location.search).get('mode') === 'radial' ? 'radial' : 'force');
  }).catch(function () { document.getElementById('vinfo').textContent = 'No se pudieron cargar los datos.'; });

  function radius(n) { return n.r || 3 + Math.min(14, Math.sqrt(n.inlinks) * 2) + (n.start ? 4 : 0); }
  window.show = function (m) {
    mode = m;
    ['force', 'tree', 'radial'].forEach(function (k) { var b = document.getElementById('b-' + k); if (b) b.className = m === k ? '' : 'secondary'; });
    if (!data) return;
    var W = canvas.width, H = canvas.height;
    nodes = data.nodes.map(function (n, i) { return Object.assign({}, n, {i: i, x: W / 2 + (Math.random() - .5) * W * .6, y: H / 2 + (Math.random() - .5) * H * .6, vx: 0, vy: 0}); });
    if (m === 'tree') {
      edges = Object.keys(data.parent).map(function (c) { return [data.parent[c], +c]; });
      layoutTree(); scale = 1; ox = 0; oy = 0; draw();
    } else if (m === 'radial') {
      layoutRadial(); scale = 1; ox = 0; oy = 0; draw();
    } else {
      edges = data.edges; scale = 1; ox = 0; oy = 0; userView = false; running = 300; tick();
    }
  };
  // «Mapa de URLs»: the start page in the centre and one bubble per section (first folder of the path), sized by
  // its number of pages and coloured by its worst status. Click a section to unfold its pages around it.
  var expanded = {};
  function sectionOf(path) {
    var seg = (path || '/').split('/').filter(Boolean);
    return seg.length ? seg[0] : '';
  }
  function layoutRadial() {
    var W = canvas.width, H = canvas.height, all = data.nodes, groups = {}, home = data.root;
    all.forEach(function (n, i) {
      if (i === home) return;
      var k = sectionOf(n.path); (groups[k] = groups[k] || []).push(i);
    });
    var loose = [];  // one-page sections (pages hanging from the root) go together
    Object.keys(groups).forEach(function (k) { if (groups[k].length < 2) { loose = loose.concat(groups[k]); delete groups[k]; } });
    if (loose.length) groups['\u0000'] = loose;
    var keys = Object.keys(groups).sort(function (a, b) { return groups[b].length - groups[a].length; });
    var rank = {error: 4, redirect: 3, blocked: 2, nonindexable: 1, indexable: 0};
    nodes = []; edges = [];
    var cx = W / 2, cy = H / 2, R1 = Math.min(W, H) * .3;
    var h = all[home] || all[0];
    nodes.push(Object.assign({}, h, {x: cx, y: cy, r: 16, label: 'Inicio', start: true}));
    keys.forEach(function (k, j) {
      var ids = groups[k], a = -Math.PI / 2 + j / keys.length * 2 * Math.PI;
      var worst = ids.reduce(function (g, i) { return rank[all[i].group] > rank[g] ? all[i].group : g; }, 'indexable');
      var sx = cx + Math.cos(a) * R1, sy = cy + Math.sin(a) * R1, si = nodes.length;
      nodes.push({section: k, x: sx, y: sy, r: 6 + Math.sqrt(ids.length) * 2.5, group: worst,
        label: (k === '\u0000' ? 'Páginas sueltas' : '/' + (k.length > 24 ? k.slice(0, 22) + '…' : k) + '/') + ' (' + ids.length + ')'});
      edges.push([0, si]);
      if (!expanded[k]) return;
      var shown = ids.slice().sort(function (x, y) { return all[y].inlinks - all[x].inlinks; }).slice(0, 60);
      var sr = nodes[si].r, R2 = sr + 14 + Math.sqrt(shown.length) * 7;  // a compact ring around its section
      shown.forEach(function (i, m) {
        var b = a + m / shown.length * 2 * Math.PI;
        nodes.push(Object.assign({}, all[i], {x: sx + Math.cos(b) * R2, y: sy + Math.sin(b) * R2, r: 3 + Math.min(5, Math.sqrt(all[i].inlinks) / 2)}));
        edges.push([si, nodes.length - 1]);
      });
    });
  }  function layoutTree() {
    var kids = {}, W = canvas.width, H = canvas.height;
    edges.forEach(function (e) { (kids[e[0]] = kids[e[0]] || []).push(e[1]); });
    var levels = [], root = data.root; if (root == null) return;
    var q = [[root, 0]], seen = {}; seen[root] = 1;
    while (q.length) { var it = q.shift(); (levels[it[1]] = levels[it[1]] || []).push(it[0]);
      (kids[it[0]] || []).forEach(function (k) { if (!seen[k]) { seen[k] = 1; q.push([k, it[1] + 1]); } }); }
    levels.forEach(function (lv, d) { lv.forEach(function (id, j) { nodes[id].x = (j + 1) * W / (lv.length + 1); nodes[id].y = 40 + d * Math.min(140, (H - 80) / Math.max(1, levels.length - 1)); }); });
    nodes.forEach(function (n) { if (!seen[n.i]) { n.x = 20 + Math.random() * (W - 40); n.y = H - 20; } });
  }
  var degFor = null, degNodes = null, userView = false;
  function fit() {  // zoom so that every bubble is inside the canvas
    if (!nodes.length) return;
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    nodes.forEach(function (n) { var r = radius(n); x0 = Math.min(x0, n.x - r); y0 = Math.min(y0, n.y - r); x1 = Math.max(x1, n.x + r); y1 = Math.max(y1, n.y + r); });
    var W = canvas.width, H = canvas.height, pad = 20;
    scale = Math.min(1, (W - 2 * pad) / Math.max(1, x1 - x0), (H - 2 * pad) / Math.max(1, y1 - y0));
    ox = W / 2 - (x0 + x1) / 2 * scale; oy = H / 2 - (y0 + y1) / 2 * scale;
  }
  function tick() {
    var W = canvas.width, H = canvas.height, k = Math.sqrt(W * H / Math.max(1, nodes.length)) * .6;
    for (var a = 0; a < nodes.length; a++) { var na = nodes[a];
      for (var b = a + 1; b < nodes.length; b++) { var nb = nodes[b], dx = na.x - nb.x, dy = na.y - nb.y, d2 = dx * dx + dy * dy + .01;
        if (d2 > 90000) continue; var f = k * k / d2 * .05, gap = radius(na) + radius(nb) + 6;
        if (d2 < gap * gap) f += (gap - Math.sqrt(d2)) / Math.sqrt(d2) * .5;  // bubbles must not overlap
        na.vx += dx * f; na.vy += dy * f; nb.vx -= dx * f; nb.vy -= dy * f; } }
    if (degFor !== edges || degNodes !== nodes) { degFor = edges; degNodes = nodes; nodes.forEach(function (n) { n.deg = 0; }); edges.forEach(function (e) { nodes[e[0]].deg++; nodes[e[1]].deg++; }); }
    edges.forEach(function (e) { var s = nodes[e[0]], t = nodes[e[1]], dx = t.x - s.x, dy = t.y - s.y, d = Math.sqrt(dx * dx + dy * dy) + .01,
      f = (d - k * .6) / d * .02 / Math.max(1, Math.min(s.deg, t.deg));  // a menu linked from every page must not pull them into one lump
      s.vx += dx * f; s.vy += dy * f; t.vx -= dx * f; t.vy -= dy * f; });
    nodes.forEach(function (n) { n.vx += (W / 2 - n.x) * .002; n.vy += (H / 2 - n.y) * .002;
      if (n !== drag) { n.x += Math.max(-20, Math.min(20, n.vx)); n.y += Math.max(-20, Math.min(20, n.vy)); } n.vx *= .6; n.vy *= .6; });
    if (running % 15 === 0 && !drag && !userView) fit();
    draw(); if (running-- > 0) requestAnimationFrame(tick);
  }
  function draw() {
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(scale, 0, 0, scale, ox, oy);
    ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue('--border') || '#ccc'; ctx.lineWidth = 1 / scale;
    ctx.globalAlpha = mode === 'radial' ? .6 : 1;
    ctx.beginPath(); edges.forEach(function (e) { var s = nodes[e[0]], t = nodes[e[1]]; ctx.moveTo(s.x, s.y); ctx.lineTo(t.x, t.y); }); ctx.stroke();
    ctx.globalAlpha = 1;
    nodes.forEach(function (n) { ctx.beginPath(); ctx.fillStyle = COLORS[n.group] || '#888'; ctx.arc(n.x, n.y, radius(n), 0, 6.283); ctx.fill();
      if (n.start || n === hover) { ctx.lineWidth = 3 / scale; ctx.strokeStyle = '#ff8bd1'; ctx.stroke(); } });
    if (mode === 'radial') {  // section names next to their bubble
      ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--text') || '#222';
      ctx.font = (12 * devicePixelRatio / scale) + 'px sans-serif'; ctx.textAlign = 'center';
      nodes.forEach(function (n) { if (n.label) ctx.fillText(n.label, n.x, n.y - radius(n) - 6 / scale); });
    }
  }
  function at(ev) { var r = canvas.getBoundingClientRect(), x = ((ev.clientX - r.left) * devicePixelRatio - ox) / scale, y = ((ev.clientY - r.top) * devicePixelRatio - oy) / scale;
    for (var i = nodes.length - 1; i >= 0; i--) { var n = nodes[i]; if ((n.x - x) * (n.x - x) + (n.y - y) * (n.y - y) <= Math.pow(radius(n) + 2, 2)) return n; } return null; }
  var pan = null;
  canvas.addEventListener('mousedown', function (ev) { drag = at(ev); if (!drag) pan = [ev.clientX, ev.clientY, ox, oy]; });
  window.addEventListener('mouseup', function () { if (drag && mode === 'force') { running = 60; tick(); } drag = null; pan = null; });
  canvas.addEventListener('mousemove', function (ev) {
    if (drag) { var r = canvas.getBoundingClientRect(); drag.x = ((ev.clientX - r.left) * devicePixelRatio - ox) / scale; drag.y = ((ev.clientY - r.top) * devicePixelRatio - oy) / scale; draw(); return; }
    if (pan) { ox = pan[2] + (ev.clientX - pan[0]) * devicePixelRatio; oy = pan[3] + (ev.clientY - pan[1]) * devicePixelRatio; draw(); return; }
    var n = at(ev); hover = n; draw();
    if (n) { tip.hidden = false; tip.style.left = (ev.offsetX + 12) + 'px'; tip.style.top = (ev.offsetY + 12) + 'px';
      tip.textContent = n.section != null ? n.label + ' · clic para ver u ocultar sus páginas'
        : n.path + ' · ' + (n.status || 'sin respuesta') + ' · ' + n.inlinks + ' inlinks' + (n.depth != null ? ' · profundidad ' + n.depth : ''); }
    else tip.hidden = true;
  });
  canvas.addEventListener('click', function (ev) { var n = at(ev);
    if (n && n.section != null) { expanded[n.section] = !expanded[n.section]; var s = scale, x = ox, y = oy; layoutRadial(); scale = s; ox = x; oy = y; draw(); return; }
    if (n) location.href = '/crawls/' + pageData().crawlId + '/page?url=' + encodeURIComponent(n.url); });
  canvas.addEventListener('wheel', function (ev) { ev.preventDefault(); userView = true; var r = canvas.getBoundingClientRect(), mx = (ev.clientX - r.left) * devicePixelRatio, my = (ev.clientY - r.top) * devicePixelRatio,
    f = ev.deltaY < 0 ? 1.15 : 1 / 1.15; ox = mx - (mx - ox) * f; oy = my - (my - oy) * f; scale *= f; draw(); }, {passive: false});
})();
