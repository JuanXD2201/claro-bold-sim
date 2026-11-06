// Reglas: editar umbrales y modo con vista previa de los movimientos que propondría antes de guardar.
import { get, put, post } from '../api.js';
import { refresh } from '../store.js';
import { h, clear, add, toast, debounce } from '../util.js';
import { stateBox, simLink } from '../ui.js';

export default {
  mount(root) {
    let saved = null, form = null;
    const inputs = {};
    const errBox = h('div', { role: 'alert', class: 'sev-critical' });
    const previewBox = h('div', { class: 'panel' });
    const formBox = h('div', { class: 'panel' });
    root.append(h('h1', null, 'Reglas del orquestador'), h('div', { class: 'split' }, formBox, previewBox));

    const field = (key, label, attrs, hint) => {
      const inp = h('input', { type: 'number', id: `r-${key}`, ...attrs, oninput: () => { form[key] = Number(inp.value); changed(); } });
      inputs[key] = inp;
      return h('label', null, label, inp, hint ? h('span', { class: 'muted' }, hint) : null);
    };
    const select = (key, label, opts, hint) => {
      const s = h('select', { id: `r-${key}`, onchange: () => { form[key] = s.value; changed(); } }, opts.map(([v, t]) => h('option', { value: v }, t)));
      inputs[key] = s;
      return h('label', null, label, s, hint ? h('span', { class: 'muted' }, hint) : null);
    };

    function buildForm() {
      clear(formBox).append(h('h2', null, 'Configuración'),
        h('div', { style: { display: 'grid', gap: '10px', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))' } },
          field('warn_threshold', 'Umbral de aviso (fracción)', { step: '0.05', min: 0.05, max: 1.5 }, 'Con 0,8 alerta y empieza a planificar al 80% de la capacidad.'),
          field('critical_threshold', 'Umbral crítico / objetivo (fracción)', { step: '0.05', min: 0.05, max: 2 }, 'Se mueven SIMs hasta que la proyección quede bajo este valor.'),
          select('mode', 'Modo', [['supervised', 'Supervisado (requiere aprobación)'], ['automatic', 'Automático (ejecuta solo)']]),
          select('trigger', 'Disparador', [['projected', 'Proyectado al cierre'], ['real', 'Consumo real']], 'Real espera a que el consumo ya supere el umbral.'),
          field('min_history_days', 'Días mínimos de historial', { min: 1, max: 29, step: 1 }, 'Antes de este día no se proyecta.'),
          field('max_moves_per_cycle', 'Máx. movimientos por ciclo', { min: 1, max: 60, step: 1 }),
          field('cooldown_days', 'Enfriamiento tras mover (días)', { min: 0, max: 15, step: 1 }, 'Evita mover la misma SIM de ida y vuelta.')),
        errBox, h('div', { class: 'row', style: { marginTop: '12px' } },
          h('button', { class: 'primary', id: 'rules-save', onclick: save }, 'Guardar reglas'), h('button', { onclick: () => { form = { ...saved }; fill(); changed(); } }, 'Descartar cambios'),
          h('span', { id: 'rules-dirty', class: 'badge warn', hidden: true }, 'cambios sin guardar')));
      fill();
    }
    function fill() { for (const [k, i] of Object.entries(inputs)) i.value = form[k]; }
    function dirty() { return saved && form && Object.keys(saved).some((k) => saved[k] !== form[k]); }
    function changed() { document.getElementById('rules-dirty').hidden = !dirty(); preview(); }

    async function save() {
      clear(errBox);
      try { saved = await put('/orchestrator/rules', form); form = { ...saved }; fill(); toast('Reglas guardadas', 'ok'); refresh(); changed(); }
      catch (e) { errBox.textContent = `${e.message}`; toast(`Reglas inválidas: ${e.message}`, 'err'); }
    }

    let seq = 0;
    const preview = debounce(async () => {
      if (!form) return;
      const mine = ++seq;
      try {
        const p = await post('/orchestrator/rules/preview', form);
        if (mine !== seq) return;
        clear(errBox);
        add(clear(previewBox), h('h2', null, 'Vista previa (no se guarda)'),
          h('p', { class: 'muted' }, `Con esta configuración y el estado actual (día ${p.day}) el orquestador propondría ${p.proposals.length} movimiento(s).`),
          p.note ? h('div', { class: 'badge warn' }, p.note) : null,
          p.proposals.length ? h('div', { class: 'tablewrap' }, h('table', null, h('thead', null, h('tr', null, ['SIM', 'Comercio', 'Ruta', 'Motivo'].map((t) => h('th', null, t)))),
            h('tbody', null, p.proposals.map((x) => h('tr', null, h('td', null, simLink(x.iccid)), h('td', null, x.merchant), h('td', null, `${x.from_pool} → ${x.to_pool}`), h('td', { class: 'wrap' }, x.reason)))))) : h('div', { class: 'state' }, 'Ningún movimiento con esta configuración.'));
      } catch (e) {
        if (mine !== seq) return;
        errBox.textContent = e.message;
        clear(previewBox).append(h('h2', null, 'Vista previa'), h('div', { class: 'state err', role: 'alert' }, `No se puede calcular: ${e.message}`));
      }
    }, 250);

    async function update() {
      if (!saved) {
        stateBox(formBox, 'loading');
        try { saved = await get('/orchestrator/rules'); form = { ...saved }; buildForm(); }
        catch (e) { stateBox(formBox, 'error', `No se pudieron cargar las reglas: ${e.message}`, update); return; }
      }
      preview();       // el estado de la simulación cambia: recalcula la vista previa con el formulario actual
    }
    return { update };
  },
};
