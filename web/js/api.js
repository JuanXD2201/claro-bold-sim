// Cliente HTTP de la UI. Marca las llamadas con X-Client: ui (las lecturas no se registran ni consumen límite de tasa).
export class ApiError extends Error {
  constructor(status, body, retryAfter) {
    const e = body && body.error;
    super(e ? e.message : `HTTP ${status}`);
    this.status = status;
    this.code = e ? e.code : 'http_error';
    this.details = e ? e.details : null;
    this.retryAfter = retryAfter;
  }
}

export async function api(method, path, body, headers = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { 'X-Client': 'ui', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(0, { error: { code: 'network', message: 'No hay conexión con el servidor local' } });
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new ApiError(res.status, data, res.headers.get('retry-after'));
  return data;
}

export const get = (p) => api('GET', p);
export const post = (p, b = {}) => api('POST', p, b);
export const put = (p, b) => api('PUT', p, b);
export const del = (p) => api('DELETE', p);
