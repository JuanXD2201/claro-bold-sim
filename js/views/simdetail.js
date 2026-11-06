// Panel lateral con el detalle de una SIM: datos, consumo, movimientos, alertas, auditoría y acciones.
import { get, post } from '../api.js';
import { refresh } from '../store.js';
import { h, clear, fmt, toast, parseHash, setParams, pretty, poolColor } from '../util.js';
import { confirmDialog, moveSims, stateBadge, sevBadge } from '../ui.js';
import { dailyChart } from '../charts.js';

const el = document.getElementById('drawer');
let iccid = null, lastFocus = null, seq = 0, data = null;

async function load() {
  const mine = ++seq;
  try {
    const d = await get(`/orchestrator/sims/${iccid}/detail`);
    if (mine === seq && iccid) { data = d; render(); }
  } catch (e) {
    if (mine === seq) { clear(el).append(closeBtn(), h('div', { class: 'state err', role: 'alert' }, e.status === 404 ? 'La SIM no existe.' : `Error: ${e.message}`)); }
  }
}

const closeBtn = () => h('button', { class: 'close', style: { float: 'right' }, 'aria-label': 'Cerrar panel', onclick: close }, 'Cerrar ✕');

async function toggleStatus(s) {
  const op = s.status === 'activa' ? 'suspend' : 'activate';
  if (op === 'suspend' && !(await confirmDialog({ title: 'Suspender SIM', danger: true, confirmLabel: 'Suspender', message: `La SIM ${s.iccid} dejará de consumir y no podrá moverse.` }))) return;
  try { await post(`/platform/sims/${s.iccid}/${op}`); toast(op === 'suspend' ? 'SIM suspendida' : 'SIM activada', 'ok'); } catch (e) { toast(`Error ${e.status}: ${e.message}`, 'err'); }
  refresh(); load();
}

async function revertLast(m) {
  if (!(await confirmDialog({ title: 'Revertir movimiento', confirmLabel: 'Revertir', message: `Se devolverá la SIM de ${m.to_pool} a ${m.from_pool} (movimiento #${m.id}).` }))) return;
  try { const r = await post(`/orchestrator/moves/${m.id}/revert`); toast(`Reversión #${r.move.id}: ${r.move.state}`, r.move.state === 'executed' ? 'ok' : 'warn'); } catch (e) { toast(`Error ${e.status}: ${e.message}`, 'err'); }
  refresh(); load();
}

function render() {
  const { sim: s, usage, moves, alerts, audit } = data;
  const lastExec = moves.find((m) => m.state === 'executed' && m.kind !== 'revert' && m.to_pool === s.pool_id);
  const rows = usage.rows.map((r) => ({ day: r.day, mb: r.mb }));
  clear(el).append(closeBtn(), h('h2', { id: 'drawer-title' }, s.merchant), h('div', { class: 'muted mono' }, s.iccid),
    h('div', { class: 'row', style: { margin: '8px 0' } },
      h('button', { class: 'primary', onclick: async () => { await moveSims([s.iccid]); load(); } }, 'Mover…'),
      h('button', { disabled: !lastExec, title: lastExec ? '' : 'No hay un movimiento ejecutado para revertir', onclick: () => revertLast(lastExec) }, 'Revertir último movimiento'),
      h('button', { class: s.status === 'activa' ? 'danger' : '', onclick: () => toggleStatus(s) }, s.status === 'activa' ? 'Suspender' : 'Activar')),
    h('dl', { class: 'kv' }, ...[['MSISDN', s.msisdn], ['Datáfono', s.terminal_id], ['Ciudad', s.city], ['Plan base', s.plan],
      ['Estado', s.status], ['Perfil', `${s.profile} (${s.base_rate} MB/día)`], ['Pool actual', h('b', { style: { color: poolColor(s.pool_id) } }, s.pool_id)], ['Pool de origen', s.origin_pool],
      ['Último movimiento', s.last_move_day == null ? '—' : `día absoluto ${s.last_move_day}`], ['Consumo del día', fmt.mb(s.daily_mb, 2)], ['Acumulado', fmt.mb(s.period_mb, 1)],
      ['Anomalía', s.anomaly ? h('span', { class: 'badge danger' }, 'sí') : 'no'], ['Mov. pendiente', s.pending_move ? 'sí' : 'no']].flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])),
    h('h3', null, 'Consumo diario y acumulado'),
    rows.length ? dailyChart(rows, `Consumo diario de la SIM ${s.iccid}`) : h('p', { class: 'muted' }, 'Aún no hay consumo en este periodo.'),
    h('h3', null, `Movimientos (${moves.length})`),
    moves.length ? h('div', { class: 'tablewrap' }, h('table', null, h('thead', null, h('tr', null, ['#', 'Ruta', 'Tipo', 'Estado'].map((t) => h('th', null, t)))),
      h('tbody', null, moves.map((m) => h('tr', null, h('td', null, m.id), h('td', null, `${m.from_pool} → ${m.to_pool}`), h('td', null, m.kind), h('td', null, stateBadge(m.state))))))) : h('p', { class: 'muted' }, 'Sin movimientos.'),
    h('h3', { style: { marginTop: '12px' } }, `Alertas (${alerts.length})`),
    alerts.length ? h('ul', null, alerts.map((a) => h('li', null, sevBadge(a.severity), ' ', a.message, ' ', stateBadge(a.state)))) : h('p', { class: 'muted' }, 'Sin alertas.'),
    h('h3', null, 'Línea de tiempo de auditoría'),
    audit.length ? h('ul', { class: 'timeline' }, audit.map((a) => h('li', null, h('b', null, a.event), ` · ${a.actor} · ${fmt.time(a.ts)} · P${a.period}`, a.detail ? h('div', { class: 'muted mono' }, JSON.stringify(a.detail)) : null))) : h('p', { class: 'muted' }, 'Sin eventos.'));
}

function close() {
  setParams({ sim: '' });
  sync();
}

function sync() {
  const next = parseHash().params.sim || null;
  if (next === iccid) return;
  iccid = next; data = null;
  if (!iccid) { el.hidden = true; clear(el); lastFocus?.focus?.(); lastFocus = null; return; }
  lastFocus = document.activeElement;
  el.hidden = false;
  clear(el).append(closeBtn(), h('div', { class: 'state', role: 'status' }, 'Cargando…'));
  load().then(() => el.querySelector('.close')?.focus());
}

export const drawer = { sync, close, update() { if (iccid) load(); } };
