import { claimInviteJti } from "@/lib/redeem";
import { verifyInvite } from "@/lib/invite";
import {
  createSessionToken,
  sessionCookieHeader,
} from "@/lib/session";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function signingSecret(): string {
  return process.env.INVITE_SIGNING_SECRET?.trim() || "";
}

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("t") || "";
  const secret = signingSecret();
  if (!secret) {
    return NextResponse.redirect(
      new URL("/login?reason=misconfigured", url.origin),
    );
  }
  if (!token) {
    return NextResponse.redirect(
      new URL("/login?reason=invite_required", url.origin),
    );
  }

  let payload;
  try {
    payload = await verifyInvite(secret, token);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "invalid";
    const reason =
      msg === "expired"
        ? "expired"
        : msg === "bad signature" || msg === "malformed token"
          ? "invalid"
          : "invalid";
    return NextResponse.redirect(
      new URL(`/login?reason=${reason}`, url.origin),
    );
  }

  let claim;
  try {
    claim = await claimInviteJti(payload.jti);
  } catch {
    return NextResponse.redirect(
      new URL("/login?reason=misconfigured", url.origin),
    );
  }
  if (claim === "already") {
    return NextResponse.redirect(
      new URL("/login?reason=already_used", url.origin),
    );
  }

  const sessionToken = await createSessionToken(payload.label, payload.jti);
  const secure = url.protocol === "https:";
  const res = NextResponse.redirect(new URL("/", url.origin));
  res.headers.append("Set-Cookie", sessionCookieHeader(sessionToken, secure));
  return res;
}
