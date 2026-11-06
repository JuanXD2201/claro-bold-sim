// Pools: tarjeta por pool con barra, proyección, SIMs arrastrables y top consumidoras.
import { get, post } from '../api.js';
import { refresh } from '../store.js';
import { h, clear, fmt, poolColor, levelClass, levelText, toast, parseHash } from '../util.js';
import { stateBox, confirmDialog, openSim, moveSims, simLink } from '../ui.js';

export default {
  mount(root, params) {
    const grid = h('div', { class: 'pools' });
    root.append(h('h1', null, 'Pools'), h('p', { class: 'muted' }, 'Arrastra una SIM a otro pool para moverla (con confirmación). Con teclado: abre la SIM y usa «Mover».'), grid);
    let first = true, focus = params.focus;
    const open = new Set();

    async function update() {
      if (first) stateBox(grid, 'loading');
      try {
        const [pools, sims] = await Promise.all([get('/orchestrator/pools'), get('/platform/sims?page_size=200&sort=period_mb&order=desc')]);
        first = false;
        render(pools.items, sims.items);
      } catch (e) { if (first) stateBox(grid, 'error', `No se pudieron cargar los pools: ${e.message}`, update); }
    }

    function bar(p) {
      if (p.is_overflow) return null;
      const max = Math.max(1.2, p.ratio || 0, p.projected_ratio || 0);
      const pos = (v) => `${Math.min(100, (v / max) * 100)}%`;
      return h('div', { class: `bar ${p.level === 'critical' ? 'critical' : p.level === 'warn' ? 'warn' : ''}`, role: 'img',
        'aria-label': `Uso ${fmt.pct(p.ratio)} de la capacidad; proyección ${fmt.pct(p.projected_ratio)}` },
        h('div', { class: 'fill', style: { width: pos(p.ratio || 0) } }),
        h('span', { class: 'tick', style: { left: pos(0.8) }, title: '80%' }), h('span', { class: 'tick', style: { left: pos(1) }, title: '100%' }),
        p.projected_ratio != null ? h('span', { class: 'proj', style: { left: pos(p.projected_ratio) }, title: `Proyección ${fmt.pct(p.projected_ratio)}` }) : null);
    }

    function card(p, sims) {
      const mine = sims.filter((s) => s.pool_id === p.id);
      const chips = h('div', { class: 'chips', role: 'list' }, mine.map((s) => h('span', { role: 'listitem', tabindex: 0, class: `chip ${s.profile} ${s.status === 'suspendida' ? 'susp' : ''} ${s.anomaly ? 'anom' : ''}`,
        draggable: true, title: `${s.merchant} · ${s.profile} · ${fmt.mb(s.period_mb, 1)} acumulado`, 'aria-label': `SIM ${s.iccid}, ${s.profile}. Enter abre el detalle.`,
        ondragstart: (ev) => { ev.dataTransfer.setData('text/plain', s.iccid); ev.dataTransfer.effectAllowed = 'move'; },
        onclick: () => openSim(s.iccid), onkeydown: (ev) => { if (ev.key === 'Enter') openSim(s.iccid); } }, fmt.iccid(s.iccid))));
      const det = h('details', { open: open.has(p.id), ontoggle: (ev) => (ev.target.open ? open.add(p.id) : open.delete(p.id)) }, h('summary', null, `SIMs (${mine.length})`), chips);
      const el = h('section', { class: 'card pool', id: `pool-${p.id}`, 'aria-label': p.id },
        h('header', null, h('h2', { style: { margin: 0, color: poolColor(p.id) } }, p.id),
          p.is_overflow ? h('span', { class: 'badge' }, 'desborde') : h('span', { class: `badge ${levelClass(p.level)}` }, levelText(p.level))),
        p.is_overflow ? h('p', { class: 'muted' }, 'Capacidad 0: todo su consumo es excedente.') : null,
        bar(p),
        h('div', { class: 'nums' }, h('span', null, 'Uso ', h('b', null, fmt.mb(p.used_mb, 0)), p.is_overflow ? '' : ` de ${fmt.mb(p.capacity_mb, 0)} (${fmt.pct(p.ratio)})`),
          p.is_overflow ? null : h('span', null, 'Proyección ', h('b', null, fmt.mb(p.projected_mb, 0)), ` (${fmt.pct(p.projected_ratio)})`),
          h('span', null, 'Excedente ', h('b', null, fmt.mb(p.excess_mb, 0))), h('span', null, 'Ritmo ', h('b', null, p.daily_rate_mb == null ? '—' : fmt.mb(p.daily_rate_mb, 1) + '/día')),
          p.pending_in || p.pending_out ? h('span', null, `Pendientes: +${p.pending_in} / −${p.pending_out}`) : null),
        h('h3', { style: { marginTop: '10px' } }, 'Top consumidoras'),
        p.top_sims.length ? h('ol', { style: { margin: '0 0 8px', paddingLeft: '20px' } }, p.top_sims.map((s) => h('li', null, simLink(s.iccid), ` ${s.merchant} — ${fmt.mb(s.period_mb, 1)}`))) : h('p', { class: 'muted' }, 'Sin SIMs.'),
        det);
      el.addEventListener('dragover', (ev) => { ev.preventDefault(); el.classList.add('drop'); });
      el.addEventListener('dragleave', () => el.classList.remove('drop'));
      el.addEventListener('drop', async (ev) => {
        ev.preventDefault(); el.classList.remove('drop');
        const iccid = ev.dataTransfer.getData('text/plain');
        const sim = sims.find((s) => s.iccid === iccid);
        if (!sim || sim.pool_id === p.id) return;
        const ok = await confirmDialog({ title: 'Mover SIM', confirmLabel: 'Mover', message: `¿Mover la SIM ${fmt.iccid(iccid)} (${sim.merchant}) de ${sim.pool_id} a ${p.id}?` });
        if (!ok) return;
        try {
          const r = await post('/orchestrator/moves', { iccid, pool_id: p.id, reason: 'Arrastrar y soltar' });
          toast(`#${r.move.id} ${r.move.state} · API ${r.api?.status ?? '—'}${r.api?.latency_ms ? ' · ' + fmt.ms(r.api.latency_ms) : ''}`, r.move.state === 'executed' ? 'ok' : 'warn');
        } catch (e) { toast(`Error ${e.status}: ${e.message}`, 'err'); }
        refresh();
      });
      return el;
    }

    function render(pools, sims) {
      clear(grid);
      if (!pools.length) { stateBox(grid, 'empty', 'No hay pools.'); return; }
      pools.forEach((p) => grid.append(card(p, sims)));
      if (focus) { const t = document.getElementById(`pool-${focus}`); if (t) { open.add(focus); t.scrollIntoView({ block: 'center' }); t.style.outline = '2px solid var(--accent)'; } focus = null; }
    }
    return { update, onParams(p) { focus = p.focus; update(); } };
  },
};
