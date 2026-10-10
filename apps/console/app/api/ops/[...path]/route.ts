import type { NextRequest } from "next/server";

// Proxy to ops-api. Container Apps authentication signs the user in and injects their
// Driftline API access token; it is forwarded as a bearer token and never reaches the browser.
const OPS_API_URL = process.env.OPS_API_URL ?? "http://ops-api";
const FORWARD = ["content-type", "accept", "traceparent", "tracestate"];

async function proxy(req: NextRequest, ctx: RouteContext<"/api/ops/[...path]">) {
  const { path } = await ctx.params;
  const url = `${OPS_API_URL}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  for (const h of FORWARD) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const token = req.headers.get("x-ms-token-aad-access-token");
  if (token) headers.set("authorization", `Bearer ${token}`);
  const upstream = await fetch(url, {
    method: req.method,
    headers,
    body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
    cache: "no-store",
    signal: req.signal,
  });
  const out = new Headers();
  for (const h of ["content-type", "cache-control"]) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  out.set("x-accel-buffering", "no");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = proxy;
export const POST = proxy;
