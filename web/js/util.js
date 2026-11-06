// Utilidades compartidas: DOM seguro (sin innerHTML), formato, toasts, hash/URL, CSV.
const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'selected', 'draggable', 'tabIndex', 'open', 'readOnly', 'multiple', 'indeterminate']);

function appendKids(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

function build(el, attrs, kids) {
  const props = [];
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (PROPS.has(k)) props.push([k, v]);
    else el.setAttribute(k, v === true ? '' : v);
  }
  appendKids(el, kids);
  for (const [k, v] of props) el[k] = v;   // value/selected después de los hijos (select)
  return el;
}

export const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
export const svg = (tag, attrs, ...kids) => build(document.createElementNS('http://www.w3.org/2000/svg', tag), attrs, kids);
export const clear = (el) => { el.replaceChildren(); return el; };
/** Como el.append, pero ignora null/false (el append nativo los escribiría como texto "null"). */
export const add = (el, ...kids) => { appendKids(el, kids); return el; };

export const fmt = {
  mb: (v, d = 1) => (v == null ? '—' : Number(v).toLocaleString('es-CO', { maximumFractionDigits: d, minimumFractionDigits: 0 }) + ' MB'),
  num: (v, d = 1) => (v == null ? '—' : Number(v).toLocaleString('es-CO', { maximumFractionDigits: d })),
  pct: (v, d = 0) => (v == null ? '—' : (v * 100).toLocaleString('es-CO', { maximumFractionDigits: d }) + '%'),
  ms: (v) => (v == null ? '—' : Number(v).toLocaleString('es-CO', { maximumFractionDigits: 1 }) + ' ms'),
  iccid: (i) => '…' + String(i).slice(-6),
  time: (iso) => (iso ? new Date(iso).toLocaleTimeString('es-CO', { hour12: false }) : ''),
  datetime: (iso) => (iso ? new Date(iso).toLocaleString('es-CO', { hour12: false }) : ''),
};

export function toast(msg, kind = 'info', ms = 5000) {
  const box = document.getElementById('toasts');
  const t = h('div', { class: `toast ${kind}`, role: kind === 'err' ? 'alert' : 'status' }, msg);
  box.append(t);
  while (box.children.length > 5) box.firstChild.remove();
  setTimeout(() => t.remove(), ms);
}

export const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [route, query = ''] = raw.split('?');
  return { route: route || 'summary', params: Object.fromEntries(new URLSearchParams(query)) };
}

export function buildHash(route, params) {
  const q = new URLSearchParams(Object.entries(params || {}).filter(([, v]) => v !== '' && v != null));
  return `#/${route}${q.toString() ? '?' + q : ''}`;
}

/** Actualiza parámetros de la URL sin disparar hashchange ni re-render. */
export function setParams(patch) {
  const { route, params } = parseHash();
  const next = { ...params, ...patch };
  history.replaceState(null, '', buildHash(route, next));
}

export const qs = (obj) => {
  const q = new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== '' && v != null && v !== false));
  return q.toString() ? '?' + q : '';
};

export function downloadText(name, text, type = 'text/csv') {
  const url = URL.createObjectURL(new Blob([text], { type: type + ';charset=utf-8' }));
  const a = h('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Descarga el resultado de un endpoint (funciona con servidor y con el backend en el navegador). */
export async function downloadApi(path, filename) {
  const r = await fetch(path, { headers: { 'X-Client': 'ui' } });
  if (!r.ok) { toast(`No se pudo exportar (HTTP ${r.status})`, 'err'); return; }
  downloadText(filename, await r.text());
  toast(`Descargado ${filename}`, 'ok');
}

export function toCSV(rows, cols) {
  const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  return [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n');
}

export const pretty = (v) => (typeof v === 'string' ? v : JSON.stringify(v, null, 2));
export const poolColor = (id) => ({ 'POOL-A': 'var(--c-a)', 'POOL-B': 'var(--c-b)', 'POOL-C': 'var(--c-c)', 'POOL-OVF': 'var(--c-ovf)' }[id] || 'var(--muted)');
export const levelClass = (l) => ({ ok: 'ok', warn: 'warn', critical: 'danger' }[l] || '');
export const levelText = (l) => ({ ok: 'Normal', warn: 'En umbral', critical: 'Agotado' }[l] || l);
