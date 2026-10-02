import { getCloudRunIdToken } from "@/lib/cloudrun-auth";
import {
  cloudRunOrigin,
  filterRequestHeaders,
  isEdgeOwnedPath,
  upstreamUrl,
} from "@/lib/proxy";
import { readSessionFromCookie } from "@/lib/session";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (isEdgeOwnedPath(url.pathname)) {
    return NextResponse.json({ error: "not proxied" }, { status: 404 });
  }

  const session = await readSessionFromCookie(request.headers.get("cookie"));
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let origin: string;
  try {
    origin = cloudRunOrigin(process.env.CLOUD_RUN_ORIGIN);
  } catch {
    return NextResponse.json(
      { error: "CLOUD_RUN_ORIGIN not configured" },
      { status: 503 },
    );
  }

  const idToken = await getCloudRunIdToken(origin);
  const target = upstreamUrl(origin, `${url.pathname}${url.search}`);
  const headers = filterRequestHeaders(request.headers, idToken);

  const init: RequestInit = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  const upstream = await fetch(target, init);
  const outHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (lower === "transfer-encoding") return;
    outHeaders.set(key, value);
  });

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: outHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
