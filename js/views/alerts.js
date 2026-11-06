// Alertas: severidad, tipo, filtros, reconocer/resolver y enlaces a SIM o pool.
import { get, post } from '../api.js';
import { refresh } from '../store.js';
import { h, clear, qs, toast, setParams } from '../util.js';
import { stateBox, stateBadge, sevBadge, simLink, poolLink } from '../ui.js';

const TYPES = [['umbral', 'Umbral'], ['agotado', 'Agotado'], ['anomalia', 'Anomalía'], ['api_failure', 'Falla de API'], ['rate_limit', 'Límite de tasa'], ['conciliacion', 'Conciliación']];
const selected = new Set();

export default {
  mount(root, params) {
    const stateParam = (p) => (p === 'all' ? '' : (p ?? 'open'));        // en la URL "all" = sin filtro de estado
    const f = { state: stateParam(params.state), type: params.type || '', severity: params.severity || '' };
    let items = [], first = true;
    const selects = {};
    const sel = (key, label, opts) => h('label', null, label, selects[key] = h('select', { onchange: (ev) => { f[key] = ev.target.value; setParams(f.state === '' ? { ...f, state: 'all' } : f); update(); } },
      h('option', { value: '' }, 'Todas'), opts.map(([v, t]) => h('option', { value: v, selected: f[key] === v }, t))));
    const bulk = h('div', { class: 'bulkbar', hidden: true });
    const body = h('div');
    root.append(h('h1', null, 'Alertas'), h('div', { class: 'filters' },
      sel('state', 'Estado', [['open', 'Abiertas'], ['acknowledged', 'Reconocidas'], ['resolved', 'Resueltas']]),
      sel('type', 'Tipo', TYPES), sel('severity', 'Severidad', [['critical', 'Crítica'], ['warning', 'Advertencia'], ['info', 'Info']])), bulk, body);

    async function act(kind, ids) {
      try {
        const r = (await post(`/orchestrator/alerts/${kind}`, { ids })).results;
        const bad = r.filter((x) => x.error).length;
        toast(`${r.length - bad} alerta(s) ${kind === 'resolve' ? 'resueltas' : 'reconocidas'}${bad ? ` · ${bad} sin cambios` : ''}`, bad ? 'warn' : 'ok');
      } catch (e) { toast(e.message, 'err'); }
      ids.forEach((i) => selected.delete(i)); refresh(); update();
    }

    function link(a) {
      if (a.subject_type === 'sim') return simLink(a.subject_id);
      if (a.subject_type === 'pool') return poolLink(a.subject_id);
      return h('a', { href: '#/console' }, 'Consola API');
    }

    function render() {
      clear(bulk); const ids = [...selected].filter((i) => items.some((a) => a.id === i)); bulk.hidden = !ids.length;
      if (ids.length) bulk.append(h('b', null, `${ids.length} seleccionada(s)`), h('button', { onclick: () => act('acknowledge', ids) }, 'Reconocer'), h('button', { class: 'primary', onclick: () => act('resolve', ids) }, 'Resolver'), h('button', { class: 'link', onclick: () => { selected.clear(); render(); } }, 'Limpiar'));
      clear(body);
      if (!items.length) { body.append(h('div', { class: 'state' }, f.state === 'open' ? 'No hay alertas abiertas.' : 'No hay alertas con esos filtros.')); return; }
      const all = h('input', { type: 'checkbox', 'aria-label': 'Seleccionar todas', checked: items.every((a) => selected.has(a.id)), onchange: (ev) => { items.forEach((a) => (ev.target.checked ? selected.add(a.id) : selected.delete(a.id))); render(); } });
      body.append(h('div', { class: 'tablewrap' }, h('table', null, h('thead', null, h('tr', null, h('th', null, all), ['#', 'Severidad', 'Tipo', 'Mensaje', 'Origen', 'Estado', 'Día', ''].map((t) => h('th', null, t)))),
        h('tbody', null, items.map((a) => h('tr', { class: selected.has(a.id) ? 'sel' : '' },
          h('td', null, h('input', { type: 'checkbox', 'aria-label': `Seleccionar alerta ${a.id}`, checked: selected.has(a.id), onchange: (ev) => { ev.target.checked ? selected.add(a.id) : selected.delete(a.id); render(); } })),
          h('td', null, a.id), h('td', null, sevBadge(a.severity)), h('td', null, TYPES.find((t) => t[0] === a.type)?.[1] || a.type), h('td', { class: 'wrap' }, a.message), h('td', null, link(a)),
          h('td', null, stateBadge(a.state)), h('td', { class: 'num' }, a.abs_day),
          h('td', null, a.state === 'open' ? h('button', { onclick: () => act('acknowledge', [a.id]) }, 'Reconocer') : null, ' ', a.state !== 'resolved' ? h('button', { onclick: () => act('resolve', [a.id]) }, 'Resolver') : null)))))));
    }

    async function update() {
      if (first) stateBox(body, 'loading');
      try { items = (await get('/orchestrator/alerts' + qs({ state: f.state, type: f.type, severity: f.severity }))).items; first = false; render(); }
      catch (e) { if (first) stateBox(body, 'error', `No se pudieron cargar las alertas: ${e.message}`, update); }
    }
    return { update, onParams(p) {      // el enlace cambió desde fuera (campana, otro enlace): sincroniza filtros y selectores
      f.state = stateParam(p.state); f.type = p.type || ''; f.severity = p.severity || '';
      for (const k of Object.keys(selects)) selects[k].value = f[k];
      update();
    } };
  },
};
