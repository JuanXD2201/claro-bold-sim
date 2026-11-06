// Gráficos SVG propios (sin librerías): líneas con umbrales y barras diarias + acumulado.
import { h, svg, fmt } from './util.js';

const W = 640, H = 270, M = { l: 48, r: 14, t: 12, b: 28 };

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function frame(ymax, xmax, xlabelEvery = 5) {
  const x = (v) => M.l + (v / xmax) * (W - M.l - M.r);
  const y = (v) => H - M.b - (v / ymax) * (H - M.t - M.b);
  const g = svg('g');
  for (let i = 0; i <= 4; i++) {
    const v = (ymax * i) / 4;
    g.append(svg('line', { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v), stroke: 'var(--border)', 'stroke-width': 1 }),
      svg('text', { x: M.l - 6, y: y(v) + 4, 'text-anchor': 'end' }, fmt.num(v, 0)));
  }
  for (let d = 0; d <= xmax; d += xlabelEvery) g.append(svg('text', { x: x(d), y: H - 8, 'text-anchor': 'middle' }, d));
  return { x, y, g };
}

function tipEl() {
  let t = document.querySelector('.tip');
  if (!t) { t = h('div', { class: 'tip', hidden: true }); document.body.append(t); }
  return t;
}

/** series: [{name,color,dash,points:[[x,y]]}], hlines: [{y,label,color}] */
export function lineChart({ series, hlines = [], xmax = 30, ymax, label, xTitle = 'Día del periodo' }) {
  const top = niceMax(Math.max(ymax || 0, ...hlines.map((l) => l.y), ...series.flatMap((s) => s.points.map((p) => p[1]))) * 1.05);
  const { x, y, g } = frame(top, xmax);
  const root = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label });
  root.append(g);
  for (const l of hlines) {
    root.append(svg('line', { x1: M.l, x2: W - M.r, y1: y(l.y), y2: y(l.y), stroke: l.color, 'stroke-dasharray': '6 4', 'stroke-width': 1.3 }),
      svg('text', { x: W - M.r - 2, y: y(l.y) - 3, 'text-anchor': 'end', style: `fill:${l.color}` }, l.label));
  }
  for (const s of series) {
    if (!s.points.length) continue;
    root.append(svg('polyline', { fill: 'none', stroke: s.color, 'stroke-width': s.width || 2, 'stroke-dasharray': s.dash || '', 'stroke-linejoin': 'round',
      points: s.points.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ') }));
  }
  root.append(svg('text', { x: W / 2, y: H - 0, 'text-anchor': 'middle' }, xTitle));
  const tip = tipEl();
  const hit = svg('rect', { x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: 'transparent' });
  hit.addEventListener('pointermove', (ev) => {
    const r = root.getBoundingClientRect();
    const day = Math.round(((ev.clientX - r.left) / r.width * W - M.l) / (W - M.l - M.r) * xmax);
    const rows = series.filter((s) => !s.noTip).map((s) => { const p = s.points.find((q) => q[0] === day); return p ? `${s.name}: ${fmt.mb(p[1], 0)}` : null; }).filter(Boolean);
    if (!rows.length) { tip.hidden = true; return; }
    tip.hidden = false; tip.textContent = `Día ${day} — ` + rows.join(' · ');
    tip.style.left = Math.min(ev.clientX + 12, innerWidth - 280) + 'px'; tip.style.top = ev.clientY + 12 + 'px';
  });
  hit.addEventListener('pointerleave', () => { tip.hidden = true; });
  root.append(hit);
  const legend = h('div', { class: 'legend' }, [...series.filter((s) => s.name && !s.noLegend).map((s) => h('span', null, h('i', { style: { background: s.color, ...(s.dash ? { backgroundImage: 'none', borderTop: `3px dashed ${s.color}`, height: 0 } : {}) } }), s.name)),
    ...hlines.map((l) => h('span', null, h('i', { style: { background: l.color } }), l.label))]);
  return h('div', null, root, legend);
}

/** Barras (consumo diario) + línea (acumulado, eje derecho). rows: [{day,mb}] */
export function dailyChart(rows, label) {
  const dmax = niceMax(Math.max(0.1, ...rows.map((r) => r.mb)) * 1.1);
  const cum = []; let s = 0;
  for (const r of rows) { s += r.mb; cum.push([r.day, s]); }
  const cmax = niceMax(Math.max(0.1, s) * 1.05);
  const { x, y, g } = frame(dmax, 30);
  const root = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label });
  root.append(g);
  const bw = (W - M.l - M.r) / 30 * 0.6;
  for (const r of rows) root.append(svg('rect', { x: x(r.day) - bw / 2, y: y(r.mb), width: bw, height: H - M.b - y(r.mb), fill: 'var(--accent)', opacity: 0.75 },
    svg('title', null, `Día ${r.day}: ${fmt.mb(r.mb, 2)}`)));
  const yc = (v) => H - M.b - (v / cmax) * (H - M.t - M.b);
  if (cum.length) root.append(svg('polyline', { fill: 'none', stroke: 'var(--warn)', 'stroke-width': 2, points: cum.map((p) => `${x(p[0])},${yc(p[1])}`).join(' ') }));
  root.append(svg('text', { x: W - M.r, y: M.t + 2, 'text-anchor': 'end', style: 'fill:var(--warn)' }, `Acumulado ${fmt.mb(s, 1)} (eje máx. ${fmt.num(cmax, 0)})`));
  return h('div', null, root, h('div', { class: 'legend' }, h('span', null, h('i', { style: { background: 'var(--accent)' } }), 'MB por día'), h('span', null, h('i', { style: { background: 'var(--warn)' } }), 'Acumulado')));
}
