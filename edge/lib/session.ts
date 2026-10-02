/**
 * HttpOnly session cookie after successful invite redeem (~12h).
 */

import { signInvite, verifyInvite, type InvitePayload } from "@/lib/invite";

export const SESSION_COOKIE = "sd_session";
export const SESSION_MAX_AGE_SEC = 12 * 60 * 60;

export type SessionPayload = InvitePayload & { kind: "session" };

function sessionSecret(): string {
  const s =
    process.env.SESSION_SECRET?.trim() ||
    process.env.INVITE_SIGNING_SECRET?.trim() ||
    "";
  if (!s) throw new Error("SESSION_SECRET or INVITE_SIGNING_SECRET required");
  return s;
}

export async function createSessionToken(label: string, jti: string): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  // Reuse invite signer shape; session jti is distinct from invite jti.
  return signInvite(sessionSecret(), {
    jti: `sess_${jti}_${now}`,
    label,
    iat: now,
    exp: now + SESSION_MAX_AGE_SEC,
  });
}

export async function readSessionFromCookie(
  cookieHeader: string | null,
): Promise<InvitePayload | null> {
  if (!cookieHeader) return null;
  const match = cookieHeader
    .split(";")
    .map((p) => p.trim())
    .find((p) => p.startsWith(`${SESSION_COOKIE}=`));
  if (!match) return null;
  const token = decodeURIComponent(match.slice(SESSION_COOKIE.length + 1));
  try {
    const secret =
      process.env.SESSION_SECRET?.trim() ||
      process.env.INVITE_SIGNING_SECRET?.trim() ||
      "";
    if (!secret) return null;
    return await verifyInvite(secret, token);
  } catch {
    return null;
  }
}

export function sessionCookieHeader(token: string, secure: boolean): string {
  const parts = [
    `${SESSION_COOKIE}=${encodeURIComponent(token)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${SESSION_MAX_AGE_SEC}`,
  ];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}
