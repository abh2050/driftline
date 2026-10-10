// Browser-side calls go through the console's own /api/ops proxy, which adds the signed-in
// user's token. The console never talks to Azure services directly (spec 5.2.2).
export async function ops<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/ops${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`;

export const usd = (x: number | null | undefined) =>
  x === null || x === undefined ? "–" : `$${x.toFixed(4)}`;
