// Estado global ligero: reloj, resumen y suscripción a eventos en vivo (SSE con sondeo de respaldo).
import { get } from './api.js';

const listeners = new Set();
export const store = {
  clock: null, platform: null, summary: null, live: false, error: null, version: -1,
  on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
};

let inflight = false;
let again = false;
export async function refresh() {
  if (inflight) { again = true; return; }
  inflight = true;
  try {
    const [s, sum] = await Promise.all([get('/sim/state'), get('/orchestrator/summary')]);
    store.clock = s.clock; store.platform = s.platform; store.summary = sum; store.error = null;
  } catch (e) {
    store.error = e;
  } finally {
    inflight = false;
  }
  listeners.forEach((fn) => fn());
  if (again) { again = false; setTimeout(refresh, 150); }
}

let timer = null;
function schedule() {   // fusiona ráfagas de eventos (p. ej. 20x) en una actualización cada ~300 ms
  if (timer) return;
  timer = setTimeout(() => { timer = null; refresh(); }, 300);
}

export function startLive() {
  refresh();
  if (window.__STATIC__) {                       // backend en el navegador: no hay SSE, el puente avisa con un evento
    window.addEventListener('simchange', schedule);
    return;
  }
  let es;
  try { es = new EventSource('sim/events'); } catch { es = null; }
  if (es) {
    es.addEventListener('state', (ev) => {
      store.live = true;
      const d = JSON.parse(ev.data);
      if (d.version !== store.version) { store.version = d.version; schedule(); }
    });
    es.onerror = () => { store.live = false; };
  }
  setInterval(() => { if (!store.live) refresh(); }, 2500);   // respaldo por sondeo
}
