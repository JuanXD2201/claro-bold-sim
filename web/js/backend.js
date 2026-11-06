// Arranque del backend. Si hay servidor (python -m orq) no hace nada. Si no (p. ej. GitHub Pages), ejecuta
// el simulador Python dentro del navegador con Pyodide y redirige las llamadas fetch a la API simulada.
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.2/full/';
const API = /^(platform|orchestrator|sim|healthz|openapi\.json)(\/|$)/;

function loadScript(src) {
  return new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = src; s.onload = ok; s.onerror = () => fail(new Error(`No se pudo descargar ${src}`));
    document.head.append(s);
  });
}

async function serverAvailable() {
  try {
    const r = await fetch('healthz', { cache: 'no-store' });
    return r.ok && (await r.json()).ok === true;
  } catch { return false; }
}

export async function boot(status) {
  if (await serverAvailable()) return 'server';
  window.__STATIC__ = true;
  status('Descargando el motor Python (solo la primera vez)…');
  await loadScript(PYODIDE + 'pyodide.js');
  const py = await globalThis.loadPyodide({ indexURL: PYODIDE });
  status('Instalando dependencias…');
  await py.loadPackage(['micropip', 'sqlite3']);
  await py.runPythonAsync("import micropip\nawait micropip.install('fastapi==0.115.14')");
  status('Cargando el simulador…');
  const zip = await (await fetch('orq.zip')).arrayBuffer();
  py.unpackArchive(zip, 'zip', { extractDir: '/app' });
  py.runPython("import sys\nsys.path.insert(0, '/app')");
  const wasm = py.pyimport('orq.wasm');

  const realFetch = window.fetch.bind(window);
  const basePath = new URL(document.baseURI).pathname.replace(/[^/]*$/, '');
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url, document.baseURI);
    const rel = url.pathname.startsWith(basePath) ? url.pathname.slice(basePath.length) : url.pathname.replace(/^\//, '');
    if (url.origin !== location.origin || !API.test(rel)) return realFetch(input, init);
    const method = (init.method || 'GET').toUpperCase();
    const headers = {};
    new Headers(init.headers || {}).forEach((v, k) => { headers[k] = v; });
    const out = JSON.parse(await wasm.handle(method, '/' + rel, url.search.slice(1), JSON.stringify(headers), init.body || ''));
    if (method !== 'GET') window.dispatchEvent(new Event('simchange'));
    return new Response(out.body, { status: out.status, headers: out.headers });
  };

  let busy = false;                       // reloj: equivale al bucle del servidor
  setInterval(() => {
    if (busy) return;
    busy = true;
    try { if (wasm.tick_if_due()) window.dispatchEvent(new Event('simchange')); } finally { busy = false; }
  }, 100);
  return 'browser';
}
