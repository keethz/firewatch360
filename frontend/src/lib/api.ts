export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

/** Today's date in UTC (YYYY-MM-DD); the app's dates are UTC. */
export const todayUtc = () => new Date().toISOString().slice(0, 10)
/** Keep a typed or picked date from going past today. */
export const notFuture = (v: string) => (v && v > todayUtc() ? todayUtc() : v)
