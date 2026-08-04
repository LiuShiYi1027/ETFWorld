// API 客户端：统一 fetch 封装，错误信息取 FastAPI 的 detail
async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
  });
  if (!resp.ok) {
    let msg = `${resp.status}`;
    try { msg = (await resp.json()).detail || msg; } catch { /* 保留状态码 */ }
    throw new Error(msg);
  }
  return resp.json();
}

export const get = (p) => api(p);
export const post = (p, body) => api(p, { method: 'POST', body: JSON.stringify(body ?? {}) });
export const put = (p, body) => api(p, { method: 'PUT', body: JSON.stringify(body ?? {}) });
export const patch = (p) => api(p, { method: 'PATCH' });
export const del = (p) => api(p, { method: 'DELETE' });
