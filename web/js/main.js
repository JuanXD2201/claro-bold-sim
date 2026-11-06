// Arranque: barra superior, navegación por hash, vistas, panel lateral de SIM, atajos y tema.
import { boot } from './backend.js';
import { get, post } from './api.js';
import { store, startLive, refresh } from './store.js';
import { h, clear, parseHash, buildHash, toast } from './util.js';
import { modal, confirmDialog } from './ui.js';
import { drawer } from './views/simdetail.js';
import summary from './views/summary.js';
import pools from './views/pools.js';
import sims from './views/sims.js';
import moves from './views/moves.js';
import alerts from './views/alerts.js';
import rules from './views/rules.js';
import apiconsole from './views/console.js';
import audit from './views/audit.js';

const VIEWS = { summary, pools, sims, moves, alerts, rules, console: apiconsole, audit };
const root = document.getElementById('view');
let current = null, currentName = null;

// ---------------------------------------------------------------- tema
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
try { applyTheme(localStorage.getItem('theme')); } catch { /* almacenamiento no disponible */ }
function toggleTheme() {
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const cur = document.documentElement.dataset.theme || (dark ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  try { localStorage.setItem('theme', next); } catch { /* ignorar */ }
}

// ---------------------------------------------------------------- acciones del reloj
async function act(label, fn) {
  try { await fn(); } catch (e) { toast(`${label}: ${e.message}`, 'err'); }
  refresh();
}
const actions = {
  toggle: () => act('Reproducir/pausar', () => post(store.clock.running ? '/sim/pause' : '/sim/play')),
  speed: (s) => act('Velocidad', () => post('/sim/speed', { speed: s })),
  tick: () => act('Avanzar día', async () => { const r = await post('/sim/tick'); if (r.cycle?.closed_period) toast('Periodo cerrado', 'ok'); }),
  toEnd: () => act('Avanzar a fin de periodo', () => post('/sim/advance-to-period-end')),
  async closePeriod() {
    const ok = await confirmDialog({ title: 'Reiniciar periodo', confirmLabel: 'Cerrar periodo',
      message: `Se cierra el periodo ${store.clock.period}: el consumo vuelve a cero y las SIMs en POOL-OVF regresan a su pool de origen (regla R4). Las propuestas pendientes expiran.` });
    if (ok) act('Reiniciar periodo', async () => { const r = await post('/sim/close-period'); toast(`Periodo ${r.result.closed} cerrado · ${r.result.returned} SIM(s) regresaron de desborde`, 'ok'); });
  },
  async resetAll() {
    const ok = await confirmDialog({ title: 'Reiniciar todo', danger: true, confirmLabel: 'Reiniciar todo',
      message: 'Se borran movimientos, alertas, auditoría y consumo, y se vuelve a la misma semilla inicial. No se puede deshacer.' });
    if (ok) act('Reiniciar', async () => { await post('/sim/reset'); toast('Simulación reiniciada con la misma semilla', 'ok'); });
  },
  async scenarios() {
    const list = (await get('/sim/scenarios')).items;
    const body = h('div', null, h('p', { class: 'muted' }, 'Cada escenario reinicia la simulación con la misma semilla, configura reglas/fallas y deja el reloj en pausa.'),
      list.map((s) => h('div', { class: 'panel', style: { marginBottom: '8px' } }, h('div', { class: 'row' }, h('b', null, s.name), h('span', { class: 'spacer' }),
        h('button', { class: 'primary', 'data-scn': s.key, onclick: async (ev) => {
          ev.target.disabled = true;
          try { const r = await post(`/sim/scenarios/${s.key}`); toast(`Escenario «${r.name}» activo (día ${r.clock.day})`, 'ok'); ev.target.closest('dialog').close(); refresh(); }
          catch (e) { toast(e.message, 'err'); ev.target.disabled = false; }
        } }, 'Activar')), h('div', { class: 'muted' }, s.description))));
    await modal({ title: 'Escenarios', body, actions: [{ label: 'Cerrar', value: 'x' }] });
  },
};

// ---------------------------------------------------------------- barra superior
const topbar = document.getElementById('topbar');
const el = {};
function buildTopbar() {
  const sp = (s) => h('button', { 'data-speed': s, 'aria-pressed': 'false', onclick: () => actions.speed(s), title: `Velocidad ${s}x` }, `${s}x`);
  el.clock = h('span', { class: 'clock', 'aria-live': 'off' });
  el.state = h('span', { class: 'badge' });
  el.play = h('button', { class: 'primary', onclick: actions.toggle, 'aria-label': 'Reproducir o pausar', title: 'Reproducir / pausar (barra espaciadora)' });
  el.speeds = [1, 5, 20].map(sp);
  el.mode = h('a', { class: 'badge', href: '#/rules', title: 'Modo del orquestador (clic para configurar)' });
  el.count = h('span', { class: 'count', hidden: true });
  topbar.append(
    h('span', { class: 'brand' }, 'Pools de datos · simulador'),
    h('span', { class: 'grp' }, el.clock, el.state),
    h('span', { class: 'grp', role: 'group', 'aria-label': 'Controles de simulación' }, el.play, el.speeds,
      h('button', { onclick: actions.tick, title: 'Avanzar un día (n)' }, '+1 día'),
      h('button', { onclick: actions.toEnd }, 'A fin de periodo'),
      h('button', { onclick: actions.closePeriod }, 'Reiniciar periodo'),
      h('button', { class: 'danger', onclick: actions.resetAll }, 'Reiniciar todo'),
      h('button', { onclick: actions.scenarios }, 'Escenarios')),
    h('span', { class: 'spacer' }), el.mode,
    h('a', { href: '#/alerts?state=open', class: 'bell', 'aria-label': 'Alertas abiertas', title: 'Alertas abiertas' }, h('button', { tabindex: -1, 'aria-hidden': 'true' }, 'Alertas'), el.count),
    h('button', { onclick: toggleTheme, title: 'Cambiar tema claro/oscuro' }, 'Tema'),
    h('button', { onclick: showHelp, title: 'Atajos de teclado (?)' }, '?'));
}
function updateTopbar() {
  const c = store.clock, s = store.summary;
  if (!c) { el.clock.textContent = 'Conectando…'; return; }
  el.clock.textContent = `Periodo ${c.period} · Día ${c.day}/${c.period_days}`;
  el.state.textContent = { running: 'en curso', paused: 'en pausa', period_end: 'fin de periodo' }[c.state];
  el.state.className = 'badge ' + { running: 'ok', paused: '', period_end: 'warn' }[c.state];
  el.play.textContent = c.running ? '⏸ Pausar' : '▶ Reproducir';
  el.speeds.forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset.speed) === c.speed)));
  if (s) {
    el.mode.textContent = s.mode === 'automatic' ? 'Modo automático' : 'Modo supervisado';
    el.mode.className = 'badge ' + (s.mode === 'automatic' ? 'warn' : 'info');
    el.count.hidden = !s.open_alerts;
    el.count.textContent = s.open_alerts;
  }
}

// ---------------------------------------------------------------- navegación
const NAV = [['summary', 'Resumen'], ['pools', 'Pools'], ['sims', 'SIMs'], ['moves', 'Movimientos'], ['alerts', 'Alertas'], ['rules', 'Reglas'], ['console', 'Consola API'], ['audit', 'Auditoría']];
function buildNav() {
  const nav = document.getElementById('nav');
  NAV.forEach(([k, label], i) => nav.append(h('a', { href: buildHash(k), 'data-route': k, title: `${label} (${i + 1})` }, label)));
}
function route() {
  const { route: r, params } = parseHash();
  const name = VIEWS[r] ? r : 'summary';
  document.querySelectorAll('#nav a').forEach((a) => {
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (name !== currentName) {
    current?.destroy?.();
    clear(root);
    currentName = name;
    document.title = `${NAV.find((n) => n[0] === name)[1]} · Simulador de pools`;
    current = VIEWS[name].mount(root, params);
    current.update();
    root.focus({ preventScroll: true });
  } else {
    current.onParams?.(params);
  }
  drawer.sync();
}

let busy = false;
function liveUpdate() {
  updateTopbar();
  if (busy) return;
  busy = true;
  Promise.resolve(current?.update()).catch((e) => console.error(e)).finally(() => { busy = false; });
  drawer.update();
}

// ---------------------------------------------------------------- atajos
function showHelp() {
  modal({ title: 'Atajos de teclado', body: h('ul', null,
    [['Espacio', 'Reproducir / pausar'], ['n', 'Avanzar un día'], ['1 … 8', 'Cambiar de vista'], ['/', 'Buscar SIM'], ['Esc', 'Cerrar panel lateral'], ['?', 'Esta ayuda']]
      .map(([k, d]) => h('li', null, h('kbd', null, k), ' — ', d))), actions: [{ label: 'Cerrar', value: 'x' }] });
}
document.addEventListener('keydown', (ev) => {
  const t = ev.target;
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === 'Escape' && !document.querySelector('dialog[open]')) { drawer.close(); return; }
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable || document.querySelector('dialog[open]')) return;
  if (ev.key === ' ' && t.tagName !== 'BUTTON' && t.tagName !== 'A') { ev.preventDefault(); if (store.clock) actions.toggle(); }
  else if (ev.key === 'n') actions.tick();
  else if (ev.key === '?') showHelp();
  else if (ev.key === '/') { const s = document.getElementById('sim-search'); if (s) { ev.preventDefault(); s.focus(); } else { location.hash = buildHash('sims'); setTimeout(() => document.getElementById('sim-search')?.focus(), 150); ev.preventDefault(); } }
  else if (/^[1-8]$/.test(ev.key)) location.hash = buildHash(NAV[Number(ev.key) - 1][0]);
});
document.addEventListener('simdrawer', () => drawer.sync());

const bootBox = document.getElementById('boot');
try {
  await boot((msg) => { document.getElementById('boot-msg').textContent = msg; });
} catch (e) {
  document.getElementById('boot-msg').textContent = `No se pudo iniciar el simulador: ${e.message}. Revisa tu conexión y recarga la página.`;
  throw e;
}
buildTopbar();
buildNav();
store.on(liveUpdate);
window.addEventListener('hashchange', route);
await refresh();          // el reloj y el resumen ya están cargados cuando se muestra la pantalla
route();
startLive();
bootBox.remove();
