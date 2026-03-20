export async function fetchJson(url, options = {}) {
  const resp = await fetch(url, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    ...options
  });

  const isJson = resp.headers.get('content-type')?.includes('application/json');
  const data = isJson ? await resp.json() : null;
  if (!resp.ok) {
    const error = new Error(data?.message || 'Request failed');
    error.status = resp.status;
    error.data = data;
    throw error;
  }
  return data;
}
