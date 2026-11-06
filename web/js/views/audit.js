// Auditoría: filtros por actor, evento, entidad y fecha; paginación y exportación CSV.
import { get } from '../api.js';
import { h, clear, fmt, qs, setParams, debounce } from '../util.js';
import { stateBox } from '../ui.js';

const PAGE = 100;

export default {
  mount(root, params) {
    const f = { actor: params.actor || '', event: params.event || '', entity: params.entity || '', date_from: params.date_from || '', date_to: params.date_to || '' };
    let limit = PAGE, first = true, actors = [];
    const actorSel = h('select', { id: 'au-actor', onchange: (ev) => { f.actor = ev.target.value; apply(); } }, h('option', { value: '' }, 'Todos'));
    const text = (key, label, ph) => h('label', null, label, h('input', { value: f[key], placeholder: ph, oninput: debounce((ev) => { f[key] = ev.target.value.trim(); apply(); }) }));
    const date = (key, label) => h('label', null, label, h('input', { type: 'date', value: f[key], onchange: (ev) => { f[key] = ev.target.value; apply(); } }));
    const exportLink = h('a', { class: 'badge info', download: '' }, 'Exportar CSV');
    const body = h('div');
    root.append(h('h1', null, 'Auditoría'), h('div', { class: 'filters' }, h('label', null, 'Actor', actorSel), text('event', 'Evento (prefijo)', 'move., alert., rules…'), text('entity', 'Entidad / ID', 'ICCID, id…'),
      date('date_from', 'Desde'), date('date_to', 'Hasta'), exportLink), body);

    function apply() { limit = PAGE; setParams(f); update(); }

    async function update() {
      exportLink.href = '/orchestrator/audit.csv' + qs(f);
      if (first) stateBox(body, 'loading');
      try {
        const d = await get('/orchestrator/audit' + qs({ ...f, limit }));
        first = false;
        if (!actorSel.dataset.ready || actors.join() !== d.actors.join()) {
          actors = d.actors; const cur = f.actor; clear(actorSel).append(h('option', { value: '' }, 'Todos'), ...actors.map((a) => h('option', { value: a, selected: a === cur }, a))); actorSel.dataset.ready = '1';
        }
        clear(body);
        if (!d.items.length) { body.append(h('div', { class: 'state' }, 'No hay eventos con esos filtros.')); return; }
        body.append(h('div', { class: 'tablewrap' }, h('table', null, h('thead', null, h('tr', null, ['#', 'Fecha/hora', 'Periodo·día', 'Actor', 'Evento', 'Entidad', 'Detalle'].map((t) => h('th', null, t)))),
          h('tbody', null, d.items.map((a) => h('tr', null, h('td', null, a.id), h('td', null, fmt.datetime(a.ts)), h('td', null, `P${a.period}·${a.abs_day}`), h('td', null, a.actor), h('td', { class: 'mono' }, a.event),
            h('td', { class: 'mono' }, a.entity ? `${a.entity}:${a.entity_id ?? ''}` : ''), h('td', { class: 'wrap mono' }, a.detail ? JSON.stringify(a.detail) : '')))))),
          h('div', { class: 'row', style: { margin: '8px 0' } }, h('span', { class: 'muted' }, `Mostrando ${d.items.length} de ${d.total}`),
            d.items.length < d.total ? h('button', { onclick: () => { limit += PAGE; update(); } }, 'Cargar más') : null));
      } catch (e) { if (first) stateBox(body, 'error', `No se pudo cargar la auditoría: ${e.message}`, update); }
    }
    return { update };
  },
};
