// SIMs: búsqueda, filtros combinables guardados en la URL, orden, selección múltiple y acciones masivas.
import { get, post } from '../api.js';
import { refresh } from '../store.js';
import { h, clear, fmt, toast, debounce, setParams, qs, downloadText, toCSV, poolColor } from '../util.js';
import { stateBox, confirmDialog, openSim, moveSims, summarize } from '../ui.js';

const CITIES = ['Bogotá', 'Medellín', 'Cali', 'Barranquilla', 'Bucaramanga'];
const POOLS = ['POOL-A', 'POOL-B', 'POOL-C', 'POOL-OVF'];
const COLS = [['iccid', 'ICCID'], ['msisdn', 'MSISDN'], ['merchant', 'Comercio'], ['terminal_id', 'Datáfono'], ['city', 'Ciudad'], ['pool_id', 'Pool'],
  ['status', 'Estado'], ['profile', 'Perfil'], ['daily_mb', 'MB/día', 1], ['period_mb', 'MB acum.', 1]];
const FILTER_KEYS = ['q', 'pool', 'status', 'profile', 'city', 'anomaly', 'pending', 'recent', 'dmin', 'dmax', 'pmin', 'pmax'];
const PAGE_SIZE = 25;
const selected = new Set();       // se conserva entre páginas y re-renders

export default {
  mount(root, params) {
    const f = Object.fromEntries(FILTER_KEYS.map((k) => [k, params[k] || '']));
    let sort = params.sort || 'iccid', order = params.order || 'asc', page = Number(params.page || 1);
    let data = { items: [], total: 0 }, first = true;

    const tristate = (v) => (v === '1' ? 'true' : v === '0' ? 'false' : '');   // '' = sin filtro
    const query = (extra = {}) => qs({ q: f.q, pool_id: f.pool, status: f.status, profile: f.profile, city: f.city,
      anomaly: tristate(f.anomaly), pending_move: tristate(f.pending), recently_moved: tristate(f.recent),
      min_daily: f.dmin, max_daily: f.dmax, min_period: f.pmin, max_period: f.pmax, sort, order, ...extra });
    const persist = () => setParams({ ...f, sort, order, page: page > 1 ? page : '' });

    const sel = (key, label, opts) => h('label', null, label, h('select', { id: `f-${key}`, onchange: (ev) => { f[key] = ev.target.value; page = 1; persist(); load(); } },
      h('option', { value: '' }, 'Todos'), opts.map(([v, t]) => h('option', { value: v, selected: f[key] === v }, t))));
    const tri = (key, label) => sel(key, label, [['1', 'Sí'], ['0', 'No']]);
    const num = (key, label) => h('label', null, label, h('input', { type: 'number', min: 0, step: 'any', style: { width: '90px' }, value: f[key], 'aria-label': label,
      oninput: debounce((ev) => { f[key] = ev.target.value; page = 1; persist(); load(); }) }));
    const search = h('input', { id: 'sim-search', type: 'search', placeholder: 'ICCID, MSISDN, comercio o datáfono  ( / )', value: f.q, style: { minWidth: '260px' }, 'aria-label': 'Buscar SIM',
      oninput: debounce((ev) => { f.q = ev.target.value.trim(); page = 1; persist(); load(); }) });

    const filters = h('div', { class: 'filters' }, h('label', null, 'Búsqueda', search),
      sel('pool', 'Pool', POOLS.map((p) => [p, p])), sel('status', 'Estado', [['activa', 'Activa'], ['suspendida', 'Suspendida']]),
      sel('profile', 'Perfil', [['bajo', 'Bajo'], ['medio', 'Medio'], ['alto', 'Alto']]), sel('city', 'Ciudad', CITIES.map((c) => [c, c])),
      tri('anomaly', 'Anomalía'), tri('pending', 'Mov. pendiente'), tri('recent', 'Movida recientemente'),
      num('dmin', 'MB/día mín.'), num('dmax', 'MB/día máx.'), num('pmin', 'Acum. mín.'), num('pmax', 'Acum. máx.'),
      h('button', { onclick: () => { FILTER_KEYS.forEach((k) => { f[k] = ''; }); page = 1; persist(); root.querySelectorAll('.filters select,.filters input').forEach((i) => { i.value = ''; }); load(); } }, 'Limpiar filtros'));

    const bulk = h('div', { class: 'bulkbar', hidden: true, role: 'region', 'aria-label': 'Acciones masivas' });
    const head = h('tr', null, h('th', null, h('input', { type: 'checkbox', id: 'sel-all', 'aria-label': 'Seleccionar página', onchange: (ev) => { data.items.forEach((s) => (ev.target.checked ? selected.add(s.iccid) : selected.delete(s.iccid))); renderRows(); } })),
      COLS.map(([k, label, numeric]) => h('th', { class: numeric ? 'num' : '', 'aria-sort': sort === k ? (order === 'asc' ? 'ascending' : 'descending') : 'none' },
        h('button', { onclick: () => { if (sort === k) order = order === 'asc' ? 'desc' : 'asc'; else { sort = k; order = 'asc'; } persist(); load(); } }, label + (sort === k ? (order === 'asc' ? ' ▲' : ' ▼') : '')))),
      h('th', null, 'Banderas'), h('th', null, ''));
    const tbody = h('tbody');
    const tableWrap = h('div', { class: 'tablewrap' }, h('table', null, h('caption', { class: 'muted', style: { textAlign: 'left', padding: '6px 10px' } }, 'Tabla de SIMs'), h('thead', null, head), tbody));
    const status = h('div', { class: 'row', style: { margin: '8px 0' } });
    root.append(h('h1', null, 'SIMs'), filters, bulk, tableWrap, status);

    function renderBulk() {
      clear(bulk); bulk.hidden = selected.size === 0;
      if (!selected.size) return;
      const ids = () => [...selected];
      bulk.append(h('b', null, `${selected.size} seleccionada(s)`),
        h('button', { class: 'primary', onclick: async () => { if (await moveSims(ids())) { selected.clear(); } load(); } }, 'Mover…'),
        h('button', { onclick: () => platformBulk('suspend', 'Suspender') }, 'Suspender'), h('button', { onclick: () => platformBulk('activate', 'Activar') }, 'Activar'),
        h('button', { onclick: () => exportCSV(true) }, 'Exportar selección CSV'), h('button', { class: 'link', onclick: () => { selected.clear(); renderRows(); } }, 'Limpiar selección'));
    }

    async function platformBulk(op, label) {
      const ids = [...selected];
      if (!(await confirmDialog({ title: `${label} ${ids.length} SIM(s)`, confirmLabel: label, danger: op === 'suspend', message: `Se llamará a POST /platform/sims/{iccid}/${op} por cada SIM seleccionada.` }))) return;
      const results = [];
      for (const i of ids) {
        try { await post(`/platform/sims/${i}/${op}`); results.push({}); } catch (e) { results.push({ error: { message: e.message } }); }
      }
      summarize(results); refresh(); load();
    }

    async function exportCSV(onlySelected) {
      try {
        const all = await get('/platform/sims' + query({ page: 1, page_size: 200 }));
        const rows = all.items.filter((s) => !onlySelected || selected.has(s.iccid));
        downloadText('sims.csv', toCSV(rows, ['iccid', 'msisdn', 'terminal_id', 'merchant', 'city', 'plan', 'status', 'profile', 'pool_id', 'origin_pool', 'daily_mb', 'period_mb', 'anomaly']));
        toast(`${rows.length} SIM(s) exportadas`, 'ok');
      } catch (e) { toast(`No se pudo exportar: ${e.message}`, 'err'); }
    }

    function renderRows() {
      clear(tbody);
      if (!data.items.length) {
        tbody.append(h('tr', null, h('td', { colspan: COLS.length + 3 }, h('div', { class: 'state' }, 'Ninguna SIM coincide con los filtros. ', h('button', { class: 'link', onclick: () => root.querySelector('.filters button:last-child').click() }, 'Limpiar filtros')))));
      }
      for (const s of data.items) {
        tbody.append(h('tr', { class: selected.has(s.iccid) ? 'sel' : '' },
          h('td', null, h('input', { type: 'checkbox', checked: selected.has(s.iccid), 'aria-label': `Seleccionar ${s.iccid}`, onchange: (ev) => { ev.target.checked ? selected.add(s.iccid) : selected.delete(s.iccid); renderRows(); } })),
          h('td', null, h('button', { class: 'link mono', onclick: () => openSim(s.iccid) }, s.iccid)), h('td', { class: 'mono' }, s.msisdn), h('td', null, s.merchant), h('td', { class: 'mono' }, s.terminal_id), h('td', null, s.city),
          h('td', null, h('b', { style: { color: poolColor(s.pool_id) } }, s.pool_id)),
          h('td', null, h('span', { class: `badge ${s.status === 'activa' ? 'ok' : 'warn'}` }, s.status)), h('td', null, s.profile),
          h('td', { class: 'num' }, fmt.num(s.daily_mb, 2)), h('td', { class: 'num' }, fmt.num(s.period_mb, 1)),
          h('td', null, s.anomaly ? h('span', { class: 'badge danger' }, 'anomalía') : null, ' ', s.pending_move ? h('span', { class: 'badge warn' }, 'pendiente') : null, ' ', s.recently_moved ? h('span', { class: 'badge info' }, 'movida') : null),
          h('td', null, h('button', { onclick: async () => { await moveSims([s.iccid]); load(); } }, 'Mover'))));
      }
      const all = root.querySelector('#sel-all'); if (all) all.checked = data.items.length > 0 && data.items.every((s) => selected.has(s.iccid));
      renderBulk();
    }

    function renderStatus() {
      clear(status);
      const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
      status.append(h('span', { class: 'muted' }, `${data.total} SIM(s) · página ${page}/${pages}`),
        h('button', { disabled: page <= 1, onclick: () => { page--; persist(); load(); } }, '← Anterior'), h('button', { disabled: page >= pages, onclick: () => { page++; persist(); load(); } }, 'Siguiente →'),
        h('span', { class: 'spacer' }), h('button', { onclick: () => exportCSV(false) }, 'Exportar resultado CSV'));
    }

    let seq = 0;
    async function load() {
      const mine = ++seq;
      if (first) { clear(tbody); tbody.append(h('tr', null, h('td', { colspan: COLS.length + 3 }, h('div', { class: 'state', role: 'status' }, 'Cargando…')))); }
      try {
        const d = await get('/platform/sims' + query({ page, page_size: PAGE_SIZE }));
        if (mine !== seq) return;
        data = d; first = false;
        if (page > 1 && !d.items.length) { page = 1; persist(); return load(); }
        // refresca encabezados (orden)
        COLS.forEach(([k, label], i) => { const th = head.children[i + 1]; th.setAttribute('aria-sort', sort === k ? (order === 'asc' ? 'ascending' : 'descending') : 'none'); th.firstChild.textContent = label + (sort === k ? (order === 'asc' ? ' ▲' : ' ▼') : ''); });
        renderRows(); renderStatus();
      } catch (e) { if (mine === seq) { clear(tbody); tbody.append(h('tr', null, h('td', { colspan: COLS.length + 3 }, h('div', { class: 'state err', role: 'alert' }, `Error: ${e.message} `, h('button', { onclick: load }, 'Reintentar'))))); } }
    }
    return { update: load };
  },
};
