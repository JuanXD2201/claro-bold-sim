// Movimientos: cola por estado, aprobar/rechazar/reintentar/revertir (individual, múltiple y por lote).
import { get, post } from '../api.js';
import { refresh } from '../store.js';
import { h, clear, toast, setParams } from '../util.js';
import { stateBox, stateBadge, confirmDialog, simLink, summarize, poolLink } from '../ui.js';

const STATES = [['all', 'Todos'], ['proposed', 'Propuestos'], ['approved', 'Aprobados'], ['executed', 'Ejecutados'], ['failed', 'Fallidos'], ['rejected', 'Rechazados'], ['reverted', 'Revertidos']];
const selected = new Set();

export default {
  mount(root, params) {
    let tab = params.state || 'proposed', items = [], first = true;
    const tabsEl = h('div', { class: 'tabs', role: 'tablist' });
    const bulk = h('div', { class: 'bulkbar', hidden: true });
    const body = h('div');
    root.append(h('h1', null, 'Movimientos'), tabsEl, bulk, body);

    const inTab = () => items.filter((m) => tab === 'all' || m.state === tab);

    async function run(label, path, ids, { confirmMsg, danger } = {}) {
      if (confirmMsg && !(await confirmDialog({ title: label, confirmLabel: label, danger, message: confirmMsg }))) return;
      try { const r = await post(path, { ids }); summarize(r.results); } catch (e) { toast(`${label}: ${e.message}`, 'err'); }
      ids.forEach((i) => selected.delete(i));
      refresh(); update();
    }
    async function revertBatches(ids) {
      const batches = [...new Set(items.filter((m) => ids.includes(m.id) && m.state === 'executed').map((m) => m.batch_id))];
      if (!batches.length) return toast('Selecciona movimientos ejecutados para revertir su lote', 'warn');
      if (!(await confirmDialog({ title: 'Revertir lote(s)', danger: true, confirmLabel: 'Revertir', message: `Se revertirán todos los movimientos ejecutados de ${batches.length} lote(s): ${batches.join(', ')}.` }))) return;
      let all = [];
      for (const b of batches) { try { all = all.concat((await post(`/orchestrator/moves/batches/${b}/revert`)).results); } catch (e) { toast(e.message, 'err'); } }
      summarize(all); selected.clear(); refresh(); update();
    }

    function renderTabs() {
      clear(tabsEl);
      for (const [k, label] of STATES) {
        const n = k === 'all' ? items.length : items.filter((m) => m.state === k).length;
        tabsEl.append(h('button', { role: 'tab', 'aria-selected': String(tab === k), 'aria-pressed': String(tab === k), onclick: () => { tab = k; setParams({ state: k }); render(); } }, `${label} (${n})`));
      }
    }

    function renderBulk() {
      clear(bulk); const ids = [...selected].filter((i) => items.some((m) => m.id === i)); bulk.hidden = !ids.length;
      if (!ids.length) return;
      const states = new Set(items.filter((m) => ids.includes(m.id)).map((m) => m.state));
      bulk.append(h('b', null, `${ids.length} seleccionado(s)`),
        h('button', { class: 'primary', disabled: !states.has('proposed'), onclick: () => run('Aprobar', '/orchestrator/moves/approve', ids.filter((i) => items.find((m) => m.id === i).state === 'proposed'), ids.length > 1 ? { confirmMsg: `Se aprobarán y ejecutarán los movimientos propuestos seleccionados.` } : {}) }, 'Aprobar'),
        h('button', { disabled: !states.has('proposed'), onclick: () => run('Rechazar', '/orchestrator/moves/reject', ids.filter((i) => items.find((m) => m.id === i).state === 'proposed'), { confirmMsg: 'Se rechazarán los movimientos propuestos seleccionados.', danger: true }) }, 'Rechazar'),
        h('button', { disabled: !states.has('failed'), onclick: () => run('Reintentar', '/orchestrator/moves/retry', ids.filter((i) => items.find((m) => m.id === i).state === 'failed')) }, 'Reintentar fallidos'),
        h('button', { disabled: !states.has('executed'), onclick: () => revertBatches(ids) }, 'Revertir lote'),
        h('button', { class: 'link', onclick: () => { selected.clear(); render(); } }, 'Limpiar'));
    }

    async function one(label, path, confirmMsg) {
      if (confirmMsg && !(await confirmDialog({ title: label, confirmLabel: label, message: confirmMsg }))) return;
      try { const r = await post(path); toast(`${label}: ${r.move?.state || 'ok'}${r.deferred ? ' (diferido por límite de tasa)' : ''}`, r.move?.state === 'failed' ? 'warn' : 'ok'); } catch (e) { toast(`${label}: ${e.message}`, 'err'); }
      refresh(); update();
    }

    function actionsFor(m) {
      const b = (t, fn, cls) => h('button', { class: cls || '', onclick: fn }, t);
      return h('span', { class: 'row' },
        m.state === 'proposed' ? [b('Aprobar', () => one('Aprobar', `/orchestrator/moves/${m.id}/approve`), 'primary'), b('Rechazar', () => one('Rechazar', `/orchestrator/moves/${m.id}/reject`))] : null,
        m.state === 'approved' ? b('Ejecutar', () => one('Ejecutar', `/orchestrator/moves/${m.id}/execute`)) : null,
        m.state === 'failed' ? b('Reintentar', () => one('Reintentar', `/orchestrator/moves/${m.id}/retry`)) : null,
        m.state === 'executed' && m.kind !== 'revert' ? b('Revertir', () => one('Revertir', `/orchestrator/moves/${m.id}/revert`, `Se devolverá la SIM de ${m.to_pool} a ${m.from_pool}.`)) : null);
    }

    function render() {
      renderTabs(); renderBulk();
      const rows = inTab();
      clear(body);
      if (!rows.length) { body.append(h('div', { class: 'state' }, tab === 'all' ? 'Aún no hay movimientos. Avanza la simulación o mueve una SIM.' : 'No hay movimientos en este estado.')); return; }
      const all = h('input', { type: 'checkbox', 'aria-label': 'Seleccionar todos', checked: rows.every((m) => selected.has(m.id)), onchange: (ev) => { rows.forEach((m) => (ev.target.checked ? selected.add(m.id) : selected.delete(m.id))); render(); } });
      body.append(h('div', { class: 'tablewrap' }, h('table', null,
        h('thead', null, h('tr', null, h('th', null, all), ['#', 'SIM', 'Comercio', 'Ruta', 'Tipo', 'Estado', 'Intentos', 'Motivo / error', 'Lote', 'Día', 'Acciones'].map((t) => h('th', null, t)))),
        h('tbody', null, rows.map((m) => h('tr', { class: selected.has(m.id) ? 'sel' : '' },
          h('td', null, h('input', { type: 'checkbox', 'aria-label': `Seleccionar movimiento ${m.id}`, checked: selected.has(m.id), onchange: (ev) => { ev.target.checked ? selected.add(m.id) : selected.delete(m.id); render(); } })),
          h('td', null, m.id), h('td', null, simLink(m.iccid)), h('td', null, m.merchant || ''), h('td', null, poolLink(m.from_pool), ' → ', poolLink(m.to_pool)), h('td', null, m.kind),
          h('td', null, stateBadge(m.state)), h('td', { class: 'num' }, m.attempts), h('td', { class: 'wrap' }, m.last_error ? h('span', { class: 'sev-critical' }, m.last_error) : (m.reason || '')),
          h('td', { class: 'mono' }, m.batch_id || ''), h('td', { class: 'num' }, m.updated_day), h('td', null, actionsFor(m))))))));
    }

    async function update() {
      if (first) stateBox(body, 'loading');
      try { items = (await get('/orchestrator/moves?limit=1000')).items; first = false; render(); }
      catch (e) { if (first) stateBox(body, 'error', `No se pudo cargar la cola: ${e.message}`, update); }
    }
    return { update };
  },
};
