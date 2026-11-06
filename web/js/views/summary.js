// Resumen: tarjetas, uso por pool vs capacidad (con umbrales y proyección) y reporte mensual.
import { get } from '../api.js';
import { store } from '../store.js';
import { h, clear, fmt, downloadText, pretty, poolColor, levelClass, levelText } from '../util.js';
import { stateBox } from '../ui.js';
import { lineChart } from '../charts.js';

export default {
  mount(root) {
    const cards = h('div', { class: 'cards' });
    const chartBox = h('div', { class: 'panel' });
    const projBox = h('div', { class: 'panel' });
    const repBox = h('div', { class: 'panel' });
    const engBox = h('p', { class: 'muted' });
    root.append(h('h1', null, 'Resumen'), cards, h('div', { class: 'grid2' }, chartBox, projBox), repBox, engBox);
    let reportPeriod = null, first = true;

    async function update() {
      if (first) stateBox(cards, 'loading');
      try {
        const [sum, ser, pools, cyc, rules] = await Promise.all([get('/orchestrator/summary'), get('/orchestrator/series'), get('/orchestrator/pools'), get('/orchestrator/cycle'), get('/orchestrator/rules')]);
        const c = store.clock || cyc.clock;
        if (reportPeriod == null || reportPeriod > c.period) reportPeriod = c.period;
        const rep = await get(`/orchestrator/reports/monthly?period=${reportPeriod}`);
        first = false;
        renderCards(sum); renderChart(ser, rules); renderProj(pools.items); renderReport(rep, c.period); renderEngine(cyc);
      } catch (e) {
        if (first) stateBox(cards, 'error', `No se pudo cargar el resumen: ${e.message}`, update);
      }
    }

    const card = (k, v, s) => h('div', { class: 'card' }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, v), h('div', { class: 's' }, s));
    function renderCards(s) {
      clear(cards).append(
        card('SIMs activas', `${s.active_sims}/${s.total_sims}`, 'suspendidas: ' + (s.total_sims - s.active_sims)),
        card('Consumo total', fmt.mb(s.total_used_mb, 0), 'periodo en curso'),
        card('Excedente', fmt.mb(s.total_excess_mb, 0), 'sobre la capacidad + desborde'),
        card('Movimientos pendientes', s.pending_moves + s.approved_moves, `${s.pending_moves} por aprobar · ${s.approved_moves} aprobados · ${s.failed_moves} fallidos`),
        card('Alertas abiertas', s.open_alerts, s.open_alerts ? 'requieren atención' : 'sin alertas'));
    }

    function renderChart(ser, rules) {
      const cap = ser.capacity['POOL-A'] || 800;
      const series = [];
      for (const id of ['POOL-A', 'POOL-B', 'POOL-C']) {
        const pts = ser.series[id].map((p) => [p.day, p.mb]);
        series.push({ name: id, color: poolColor(id), points: pts });
        const pj = ser.projected[id];
        if (pj != null && ser.day > 0 && ser.day < ser.period_days) {
          series.push({ name: `${id} proyección`, color: poolColor(id), dash: '5 4', width: 1.5, noLegend: true, noTip: true, points: [[ser.day, pts[pts.length - 1][1]], [ser.period_days, pj]] });
        }
      }
      series.push({ name: 'POOL-OVF (excedente)', color: poolColor('POOL-OVF'), points: ser.series['POOL-OVF'].map((p) => [p.day, p.mb]) });
      clear(chartBox).append(h('h2', null, 'Uso acumulado por pool vs capacidad'),
        lineChart({ series, xmax: ser.period_days, label: 'Consumo acumulado por pool durante el periodo, con líneas de umbral y capacidad',
          hlines: [{ y: cap * rules.warn_threshold, label: `${Math.round(rules.warn_threshold * 100)}% umbral`, color: 'var(--warn)' },
            { y: cap * rules.critical_threshold, label: `${Math.round(rules.critical_threshold * 100)}% capacidad`, color: 'var(--danger)' }] }),
        h('p', { class: 'muted' }, 'Las líneas punteadas son la proyección lineal al día 30 con el ritmo actual de las SIMs de cada pool.'));
    }

    function renderProj(items) {
      clear(projBox).append(h('h2', null, 'Proyección al cierre'), h('div', { class: 'tablewrap' }, h('table', null,
        h('thead', null, h('tr', null, ['Pool', 'Uso actual', 'Proyectado', '% capacidad', 'Estado'].map((t) => h('th', null, t)))),
        h('tbody', null, items.map((p) => h('tr', null, h('td', null, h('b', { style: { color: poolColor(p.id) } }, p.id)),
          h('td', { class: 'num' }, fmt.mb(p.used_mb, 0)), h('td', { class: 'num' }, p.is_overflow ? '—' : fmt.mb(p.projected_mb, 0)),
          h('td', { class: 'num' }, p.is_overflow ? '—' : fmt.pct(p.projected_ratio)),
          h('td', null, p.is_overflow ? h('span', { class: 'badge' }, `${p.sim_count} SIM(s) en desborde`) : h('span', { class: `badge ${levelClass(p.level)}` }, levelText(p.level)))))))));
    }

    const row = (id, cap, used, ex, util, n, mi, mo, bold) => h('tr', { style: bold ? { fontWeight: 700 } : null },
      h('td', null, id), [fmt.mb(cap, 0), fmt.mb(used, 1), fmt.mb(ex, 1), util, n, mi, mo].map((v) => h('td', { class: 'num' }, v)));
    function renderReport(rep, curPeriod) {
      const sel = h('select', { 'aria-label': 'Periodo del reporte', onchange: () => { reportPeriod = Number(sel.value); update(); } },
        Array.from({ length: curPeriod }, (_, i) => curPeriod - i).map((p) => h('option', { value: p, selected: p === rep.period }, `Periodo ${p}${p === curPeriod ? ' (en curso)' : ''}`)));
      const t = rep.totals;
      clear(repBox).append(h('div', { class: 'row' }, h('h2', { style: { margin: 0 } }, 'Reporte mensual consolidado'), sel, h('span', { class: 'spacer' }),
        h('span', { class: `badge ${rep.reconciled ? 'ok' : 'danger'}`, title: `Suma directa de consumo: ${fmt.mb(rep.direct_total_mb, 2)}` }, rep.reconciled ? `Total = Σ pools ✓ (${fmt.mb(t.used_mb, 1)})` : 'No concilia'),
        h('a', { class: 'badge info', href: `/orchestrator/reports/monthly?period=${rep.period}&format=csv`, download: '' }, 'Exportar CSV'),
        h('button', { onclick: () => downloadText(`reporte_periodo_${rep.period}.json`, pretty(rep), 'application/json') }, 'Exportar JSON')),
        h('div', { class: 'tablewrap', style: { marginTop: '8px' } }, h('table', null,
          h('thead', null, h('tr', null, ['Pool', 'Capacidad', 'Consumo', 'Excedente', 'Utilización', 'SIMs', 'Entran', 'Salen'].map((x, i) => h('th', { class: i ? 'num' : '' }, x)))),
          h('tbody', null, rep.pools.map((p) => row(p.pool_id, p.capacity_mb, p.used_mb, p.excess_mb, p.utilization_pct == null ? '—' : p.utilization_pct + '%', p.sims_current, p.moves_in, p.moves_out)),
            row('TOTAL', t.capacity_mb, t.used_mb, t.excess_mb, '', t.sims_current, t.moves_in, t.moves_out, true)))));
    }

    function renderEngine(cyc) {
      const m = cyc.metrics;
      engBox.textContent = m.cycles ? `Ciclo del motor: último ${fmt.ms(m.last_ms)} · promedio ${fmt.ms(m.avg_ms)} · máximo ${fmt.ms(m.max_ms)} (${m.cycles} ciclos medidos)` : 'Ciclo del motor: aún sin mediciones (avanza un día).';
    }
    return { update };
  },
};
