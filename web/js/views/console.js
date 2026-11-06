// Consola de API: registro en vivo, probador de endpoints y panel de condiciones (latencia, límite de tasa, fallas).
import { get, put, post, del } from '../api.js';
import { store, refresh } from '../store.js';
import { h, clear, fmt, toast, pretty } from '../util.js';
import { stateBox } from '../ui.js';

const MAX = 300;

export default {
  mount(root) {
    let entries = [], since = 0, firstLog = true, sample = '', cfgReady = false;
    const open = new Set();
    // ------------------------------------------------ registro
    const onlyErr = h('input', { type: 'checkbox', id: 'log-err', onchange: renderLog });
    const freeze = h('input', { type: 'checkbox', id: 'log-freeze', onchange: renderLog });
    const srcSel = h('select', { id: 'log-src', onchange: renderLog }, [['', 'Todos los orígenes'], ['ui', 'UI'], ['orchestrator', 'Orquestador'], ['tester', 'Probador'], ['external', 'Externo']].map(([v, t]) => h('option', { value: v }, t)));
    const logBody = h('div', { class: 'tablewrap', style: { maxHeight: '420px', overflow: 'auto' } });
    const logPanel = h('div', { class: 'panel' }, h('div', { class: 'row' }, h('h2', { style: { margin: 0 } }, 'Registro en vivo'), h('span', { class: 'spacer' }),
      h('label', { class: 'inline' }, onlyErr, 'Solo errores'), h('label', { class: 'inline' }, freeze, 'Congelar'), srcSel,
      h('button', { onclick: async () => { await del('/sim/api-log'); entries = []; since = 0; renderLog(); } }, 'Limpiar')), logBody);

    // ------------------------------------------------ probador
    const method = h('select', { id: 't-method', 'aria-label': 'Método' }, ['GET', 'POST', 'PUT', 'DELETE'].map((m) => h('option', null, m)));
    const path = h('input', { id: 't-path', value: '/platform/pools', style: { width: '100%' }, 'aria-label': 'Ruta', list: 't-paths' });
    const idem = h('input', { id: 't-idem', placeholder: 'Idempotency-Key (solo move)', 'aria-label': 'Idempotency-Key' });
    const reqBody = h('textarea', { id: 't-body', rows: 4, 'aria-label': 'Cuerpo JSON', placeholder: '{"pool_id": "POOL-C"}' });
    const out = h('div', { 'aria-live': 'polite' });
    const preset = h('select', { 'aria-label': 'Ejemplos', onchange: () => { const p = presets()[preset.value]; if (!p) return; method.value = p[0]; path.value = p[1]; reqBody.value = p[2] || ''; idem.value = p[3] || ''; } },
      h('option', { value: '' }, 'Ejemplos…'));
    const presets = () => [['GET', '/platform/pools'], ['GET', '/platform/sims?page_size=5&sort=period_mb&order=desc'], ['GET', `/platform/sims/${sample}`], ['GET', '/platform/usage'],
      ['POST', `/platform/sims/${sample}/move`, '{"pool_id": "POOL-C"}', 'demo-' + Date.now()], ['POST', '/platform/sims/bulk-move', `{"moves":[{"iccid":"${sample}","pool_id":"POOL-B"},{"iccid":"no-existe","pool_id":"POOL-B"}]}`],
      ['POST', `/platform/sims/${sample}/suspend`], ['GET', '/orchestrator/rules'], ['GET', '/orchestrator/moves?state=proposed'], ['GET', '/orchestrator/reports/monthly']];
    const send = async () => {
      const headers = { 'X-Client': 'tester' };
      let body;
      if (method.value !== 'GET' && method.value !== 'DELETE' && reqBody.value.trim()) { headers['Content-Type'] = 'application/json'; body = reqBody.value; }
      else if (method.value === 'POST') { headers['Content-Type'] = 'application/json'; body = '{}'; }
      if (idem.value.trim()) headers['Idempotency-Key'] = idem.value.trim();
      const t0 = performance.now();
      clear(out).append(h('div', { class: 'state', role: 'status' }, 'Enviando…'));
      try {
        const r = await fetch(path.value, { method: method.value, headers, body });
        const txt = await r.text(); let parsed = txt; try { parsed = pretty(JSON.parse(txt)); } catch { /* texto plano */ }
        clear(out).append(h('div', { class: 'row' }, h('span', { class: `badge ${r.ok ? 'ok' : 'danger'}` }, `HTTP ${r.status}`), h('span', { class: 'muted' }, fmt.ms(performance.now() - t0)),
          r.headers.get('retry-after') ? h('span', { class: 'badge warn' }, `Retry-After: ${r.headers.get('retry-after')}s`) : null, r.headers.get('idempotent-replay') ? h('span', { class: 'badge info' }, 'Idempotent-Replay') : null),
          h('pre', { class: 'json' }, parsed.slice(0, 6000)));
      } catch (e) { clear(out).append(h('div', { class: 'state err', role: 'alert' }, `Error de red: ${e.message}`)); }
      pullLog();
    };
    const testerPanel = h('div', { class: 'panel' }, h('h2', null, 'Probador de endpoints'), h('div', { class: 'row' }, preset, method, h('button', { class: 'primary', onclick: send }, 'Enviar')),
      h('label', { style: { marginTop: '8px' } }, 'Ruta', path), h('label', null, 'Cuerpo', reqBody), h('div', { class: 'row', style: { margin: '6px 0' } }, h('label', null, 'Cabecera', idem), h('button', { onclick: () => { idem.value = 'k-' + Math.random().toString(36).slice(2, 10); } }, 'Generar clave')), out,
      h('p', { class: 'muted' }, 'Se envía con X-Client: tester, así que cuenta para el límite de tasa y sufre las fallas inyectadas. Documentación completa en ', h('a', { href: '/docs', target: '_blank', rel: 'noopener' }, '/docs'), '.'));

    // ------------------------------------------------ condiciones
    const lat = h('input', { type: 'number', id: 'c-lat', min: 0, max: 5000, step: 10, style: { width: '90px' } });
    const rlOn = h('input', { type: 'checkbox', id: 'c-rl-on' }), rlMax = h('input', { type: 'number', id: 'c-rl-max', min: 1, max: 10000, style: { width: '90px' } }), rlRetry = h('input', { type: 'number', id: 'c-rl-retry', min: 1, max: 300, style: { width: '80px' } });
    const calls = h('span', { class: 'badge', id: 'c-calls' });
    const fScope = h('select', { id: 'f-scope' }, [['global', 'Global'], ['sim', 'Por SIM']].map(([v, t]) => h('option', { value: v }, t)));
    const fIccid = h('input', { id: 'f-iccid', placeholder: 'ICCID (si es por SIM)', style: { width: '170px' } });
    const fKind = h('select', { id: 'f-kind' }, [['transient', 'Transitoria'], ['permanent', 'Permanente']].map(([v, t]) => h('option', { value: v }, t)));
    const fStatus = h('select', { id: 'f-status' }, [['', 'Código por defecto'], ['503', '503'], ['500', '500']].map(([v, t]) => h('option', { value: v }, t)));
    const fCount = h('input', { type: 'number', id: 'f-count', min: 1, max: 100, value: 3, style: { width: '70px' }, 'aria-label': 'Cantidad de fallas' });
    const fCommit = h('input', { type: 'checkbox', id: 'f-commit' });
    const faultList = h('div'), engine = h('div');
    const apply = async (fn, ok) => { try { await fn(); toast(ok, 'ok'); } catch (e) { toast(e.message, 'err'); } refresh(); update(); };
    const condPanel = h('div', { class: 'panel' }, h('h2', null, 'Condiciones de la API simulada'),
      h('div', { class: 'row' }, h('label', null, 'Latencia base (ms)', lat), h('button', { onclick: () => apply(() => put('/sim/latency', { latency_ms: Number(lat.value) }), 'Latencia aplicada') }, 'Aplicar')),
      h('div', { class: 'row', style: { marginTop: '8px' } }, h('label', { class: 'inline' }, rlOn, 'Límite de tasa'), h('label', null, 'Llamadas por ciclo', rlMax), h('label', null, 'Retry-After (s)', rlRetry),
        h('button', { onclick: () => apply(() => put('/sim/rate-limit', { enabled: rlOn.checked, max_calls: Number(rlMax.value), retry_after_s: Number(rlRetry.value) }), 'Límite aplicado') }, 'Aplicar'), calls),
      h('h3', { style: { marginTop: '12px' } }, 'Inyectar falla'),
      h('div', { class: 'row' }, h('label', null, 'Alcance', fScope), h('label', null, 'SIM', fIccid), h('label', null, 'Tipo', fKind), h('label', null, 'Estado HTTP', fStatus), h('label', null, 'Cantidad (transitoria)', fCount),
        h('label', { class: 'inline', title: 'La plataforma aplica el cambio pero responde con error: prueba idempotencia y conciliación' }, fCommit, 'Aplicar y fallar'),
        h('button', { class: 'primary', onclick: () => apply(() => post('/sim/faults', { scope: fScope.value, iccid: fScope.value === 'sim' ? fIccid.value.trim() : null, kind: fKind.value, status: fStatus.value ? Number(fStatus.value) : null, count: Number(fCount.value), commit_then_fail: fCommit.checked }), 'Falla inyectada') }, 'Inyectar')),
      faultList, h('h3', { style: { marginTop: '12px' } }, 'Ciclos del motor (medición)'), engine);

    root.append(h('h1', null, 'Consola de API'), h('div', { class: 'split' }, logPanel, h('div', { style: { display: 'grid', gap: '12px', alignContent: 'start' } }, testerPanel, condPanel)));

    function renderLog() {
      clear(logBody);
      if (freeze.checked) return;
      let rows = entries.filter((e) => (!onlyErr.checked || e.status >= 400) && (!srcSel.value || e.source === srcSel.value));
      if (!rows.length) { logBody.append(h('div', { class: 'state' }, firstLog ? 'Cargando…' : 'Aún no hay llamadas. Mueve una SIM, avanza un día o usa el probador.')); return; }
      rows = rows.slice(-150).reverse();
      logBody.append(h('table', null, h('thead', null, h('tr', null, ['Hora', 'Origen', 'Método', 'Ruta', 'Estado', 'Latencia', ''].map((t) => h('th', null, t)))),
        h('tbody', null, rows.flatMap((e) => [h('tr', { 'data-id': e.id }, h('td', { class: 'mono' }, fmt.time(e.ts)), h('td', null, e.source), h('td', { class: 'mono' }, e.method), h('td', { class: 'mono', style: { maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis' } }, e.path),
          h('td', null, h('span', { class: `badge ${e.status < 300 ? 'ok' : e.status === 429 ? 'warn' : 'danger'}` }, e.status)), h('td', { class: 'num' }, fmt.ms(e.latency_ms)),
          h('td', null, h('button', { class: 'link', 'aria-expanded': String(open.has(e.id)), onclick: () => { open.has(e.id) ? open.delete(e.id) : open.add(e.id); renderLog(); } }, open.has(e.id) ? 'ocultar' : 'ver'))),
          open.has(e.id) ? h('tr', null, h('td', { colspan: 7, class: 'wrap' }, e.retry_after ? h('div', { class: 'badge warn' }, `Retry-After: ${e.retry_after}`) : null, h('div', { class: 'muted' }, 'Petición'), h('pre', { class: 'json' }, e.request == null ? '—' : pretty(e.request)), h('div', { class: 'muted' }, 'Respuesta'), h('pre', { class: 'json' }, pretty(e.response)))) : null]))));
    }

    async function pullLog() {
      try {
        const d = await get(`/sim/api-log?since=${since}&limit=200`);
        if (d.items.length) { entries = entries.concat(d.items).slice(-MAX); since = d.items[d.items.length - 1].id; }
        firstLog = false; renderLog();
      } catch (e) { if (firstLog) stateBox(logBody, 'error', `No se pudo leer el registro: ${e.message}`, pullLog); }
    }

    function renderConfig(c, cyc) {
      if (!cfgReady) { lat.value = c.latency_ms; rlOn.checked = c.rate_limit.enabled; rlMax.value = c.rate_limit.max_calls; rlRetry.value = c.rate_limit.retry_after_s; cfgReady = true; }
      calls.textContent = `Llamadas este ciclo: ${c.calls_in_cycle}${c.rate_limit.enabled ? ' / ' + c.rate_limit.max_calls : ''}`;
      calls.className = 'badge ' + (c.rate_limit.enabled && c.calls_in_cycle >= c.rate_limit.max_calls ? 'warn' : '');
      clear(faultList).append(c.faults.length ? h('ul', null, c.faults.map((f) => h('li', null, `#${f.id} ${f.kind} ${f.status} · ${f.scope === 'sim' ? 'SIM ' + fmt.iccid(f.iccid) : 'global'}${f.remaining != null ? ` · quedan ${f.remaining}` : ''}${f.commit_then_fail ? ' · aplica y falla' : ''} · ${f.hits} impacto(s) `,
        h('button', { class: 'link', onclick: () => apply(() => del(`/sim/faults/${f.id}`), 'Falla eliminada') }, 'quitar'))), h('li', null, h('button', { onclick: () => apply(() => del('/sim/faults'), 'Fallas limpiadas') }, 'Limpiar todas'))) : h('p', { class: 'muted' }, 'Sin fallas activas.'));
      const m = cyc.metrics;
      clear(engine).append(m.cycles ? h('div', null, h('p', { class: 'muted' }, `Último ${fmt.ms(m.last_ms)} · promedio ${fmt.ms(m.avg_ms)} · máximo ${fmt.ms(m.max_ms)} sobre ${m.cycles} ciclos`),
        h('div', { class: 'tablewrap' }, h('table', null, h('thead', null, h('tr', null, ['P·Día', 'ms', 'Llamadas API', 'Propuestos', 'Auto-ejecutados', 'Conciliados'].map((t) => h('th', { class: 'num' }, t)))),
          h('tbody', null, m.recent.slice(-8).reverse().map((r) => h('tr', null, [`${r.period}·${r.day}`, r.cycle_ms, r.api_calls, r.proposed ?? 0, r.auto_executed ?? 0, r.reconciled ?? 0].map((v) => h('td', { class: 'num' }, v)))))))) : h('p', { class: 'muted' }, 'Sin ciclos aún.'));
    }

    async function update() {
      try {
        const [cyc, sims] = await Promise.all([get('/orchestrator/cycle'), sample ? null : get('/platform/sims?page_size=1&sort=period_mb&order=desc')]);
        if (sims && sims.items[0]) { sample = sims.items[0].iccid; clear(preset).append(h('option', { value: '' }, 'Ejemplos…'), ...presets().map((p, i) => h('option', { value: i }, `${p[0]} ${p[1].length > 48 ? p[1].slice(0, 46) + '…' : p[1]}`))); }
        renderConfig(cyc.platform, cyc);
      } catch (e) { /* el registro muestra su propio error */ }
      pullLog();
    }
    return { update };
  },
};
