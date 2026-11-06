// Componentes compartidos: diálogos, estados (carga/vacío/error), movimiento de SIMs, enlaces.
import { get, post } from './api.js';
import { h, clear, fmt, toast, pretty, setParams, buildHash } from './util.js';
import { refresh } from './store.js';

/** Diálogo modal nativo (<dialog>): trampa de foco y Esc gratis. Devuelve el `value` de la acción pulsada. */
export function modal({ title, body, actions }) {
  return new Promise((resolve) => {
    const dlg = h('dialog', { 'aria-labelledby': 'dlg-title' });
    const bar = h('div', { class: 'actions' });
    for (const a of actions) {
      bar.append(h('button', { class: a.kind || '', type: 'button', onclick: async () => {
        if (a.onClick) { const keep = await a.onClick(dlg); if (keep === false) return; }
        dlg.close(String(a.value ?? a.label)); resolve(a.value ?? a.label);
      } }, a.label));
    }
    dlg.append(h('h2', { id: 'dlg-title' }, title), body, bar);
    dlg.addEventListener('close', () => { dlg.remove(); resolve(null); });
    document.body.append(dlg);
    dlg.showModal();
    (dlg.querySelector('[autofocus]') || dlg.querySelector('input,select,button.primary,button'))?.focus();
  });
}

export async function confirmDialog({ title, message, confirmLabel = 'Confirmar', danger = false }) {
  const r = await modal({ title, body: h('p', null, message), actions: [
    { label: 'Cancelar', value: false }, { label: confirmLabel, value: true, kind: danger ? 'danger' : 'primary' }] });
  return r === true;
}

export function stateBox(el, kind, msg, retry) {
  clear(el);
  el.append(h('div', { class: `state ${kind === 'error' ? 'err' : ''}`, role: kind === 'error' ? 'alert' : 'status' },
    kind === 'loading' ? 'Cargando…' : msg, kind === 'error' && retry ? h('div', null, h('button', { onclick: retry }, 'Reintentar')) : null));
}

export const sevBadge = (s) => h('span', { class: `sev-${s}` }, { critical: 'Crítica', warning: 'Advertencia', info: 'Info' }[s] || s);
export const stateBadge = (s) => h('span', { class: `badge ${{ executed: 'ok', approved: 'info', proposed: 'warn', failed: 'danger', rejected: '', reverted: '', open: 'danger', acknowledged: 'warn', resolved: 'ok' }[s] || ''}` },
  { proposed: 'propuesto', approved: 'aprobado', executed: 'ejecutado', failed: 'fallido', rejected: 'rechazado', reverted: 'revertido', open: 'abierta', acknowledged: 'reconocida', resolved: 'resuelta' }[s] || s);

export function openSim(iccid) { setParams({ sim: iccid }); document.dispatchEvent(new CustomEvent('simdrawer')); }
export const simLink = (iccid, text) => h('button', { class: 'link mono', onclick: () => openSim(iccid), title: iccid }, text || fmt.iccid(iccid));
export const poolLink = (id) => h('a', { href: buildHash('pools', { focus: id }) }, id);

export function summarize(results) {
  let ok = 0, fail = 0, deferred = 0;
  for (const r of results) {
    if (r.error) fail++;
    else if (r.deferred) deferred++;
    else if (r.move && r.move.state === 'failed') fail++;
    else ok++;
  }
  const parts = [`${ok} correcto(s)`];
  if (fail) parts.push(`${fail} con error`);
  if (deferred) parts.push(`${deferred} diferido(s) por límite de tasa`);
  toast(parts.join(' · '), fail ? 'warn' : 'ok');
}

/** Diálogo de movimiento (una o varias SIMs). `target` preselecciona el pool (arrastrar y soltar). */
export async function moveSims(iccids, { target = null } = {}) {
  const pools = (await get('/platform/pools')).items;
  const sel = h('select', { id: 'mv-target', autofocus: true }, pools.map((p) => h('option', { value: p.id, selected: p.id === target }, p.id + (p.is_overflow ? ' (desborde)' : ''))));
  const reason = h('input', { id: 'mv-reason', maxlength: 200, placeholder: 'Motivo (opcional)' });
  const result = h('div', { 'aria-live': 'polite' });
  let changed = false;
  const body = h('div', null,
    h('p', null, iccids.length === 1 ? ['Mover la SIM ', h('b', { class: 'mono' }, fmt.iccid(iccids[0]))] : `Mover ${iccids.length} SIMs`, ' a:'),
    h('div', { class: 'row' }, h('label', null, 'Pool destino', sel), h('label', null, 'Motivo', reason)),
    h('p', { class: 'muted' }, 'La acción pasa por el orquestador: queda en auditoría y en el registro de la API.'), result);
  await modal({ title: 'Mover SIM', body, actions: [
    { label: 'Cerrar', value: 'close' },
    { label: 'Mover', value: 'go', kind: 'primary', onClick: async () => {
      clear(result);
      try {
        if (iccids.length === 1) {
          const r = await post('/orchestrator/moves', { iccid: iccids[0], pool_id: sel.value, reason: reason.value || null });
          changed = true;
          result.append(h('div', { class: 'badge ' + (r.move.state === 'executed' ? 'ok' : r.move.state === 'failed' ? 'danger' : 'warn') },
            `Movimiento #${r.move.id}: ${r.move.state}` + (r.deferred ? ` (diferido, Retry-After ${r.retry_after}s)` : '')),
            r.api ? h('p', null, `Respuesta de la API: HTTP ${r.api.status}` + (r.api.latency_ms ? ` · ${fmt.ms(r.api.latency_ms)}` : '')) : null,
            r.api ? h('pre', { class: 'json' }, pretty(r.api.body)) : null);
          toast(`Movimiento #${r.move.id}: ${r.move.state}`, r.move.state === 'executed' ? 'ok' : 'warn');
        } else {
          const r = await post('/orchestrator/moves/bulk', { items: iccids.map((i) => ({ iccid: i, pool_id: sel.value, reason: reason.value || null })) });
          changed = true;
          result.append(h('p', null, `Lote ${r.batch_id}: ${r.summary.ok} correctos, ${r.summary.failed} con error`),
            h('ul', null, r.results.map((x) => h('li', { class: x.ok ? '' : 'sev-critical' }, `${fmt.iccid(x.iccid)}: ` + (x.ok ? (x.move?.state || 'ok') : (x.error?.message || 'error'))))));
          toast(`Lote ${r.batch_id}: ${r.summary.ok}/${r.summary.total} correctos`, r.summary.failed ? 'warn' : 'ok');
        }
        refresh();
      } catch (e) {
        result.append(h('div', { class: 'sev-critical', role: 'alert' }, `Error ${e.status}: ${e.message}`));
        toast(`Error ${e.status}: ${e.message}`, 'err');
      }
      return false;   // mantiene el diálogo abierto para ver el resultado
    } },
  ] });
  return changed;
}
