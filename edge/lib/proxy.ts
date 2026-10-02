/**
 * Build upstream URL + hop headers for reverse-proxy to Cloud Run demo.
 * Pure helpers — unit-tested from Python via mirrored contracts / node not required.
 */

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

export function cloudRunOrigin(raw: string | undefined | null): string {
  const origin = (raw ?? "").trim().replace(/\/+$/, "");
  if (!origin) {
    throw new Error("CLOUD_RUN_ORIGIN is required");
  }
  return origin;
}

export function upstreamUrl(origin: string, pathAndQuery: string): string {
  const base = cloudRunOrigin(origin);
  const path = pathAndQuery.startsWith("/") ? pathAndQuery : `/${pathAndQuery}`;
  return `${base}${path}`;
}

export function filterRequestHeaders(
  incoming: Headers,
  idToken: string | null,
): Headers {
  const out = new Headers();
  incoming.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower)) return;
    if (lower === "cookie") return; // do not forward edge session cookies upstream
    if (lower === "authorization") return;
    out.set(key, value);
  });
  if (idToken) {
    out.set("Authorization", `Bearer ${idToken}`);
  }
  return out;
}

/** Paths that must never be proxied (auth + edge control plane). */
export function isEdgeOwnedPath(pathname: string): boolean {
  if (pathname === "/login" || pathname.startsWith("/login/")) return true;
  if (pathname === "/invite" || pathname.startsWith("/invite/")) return true;
  if (pathname === "/api/health" || pathname.startsWith("/api/health/")) return true;
  if (pathname.startsWith("/_next")) return true;
  if (pathname === "/favicon.ico") return true;
  return false;
}
