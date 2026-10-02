/**
 * Signed invite tokens (1A). Compatible with bc_schedule_agent.invite_token.
 */

export type InvitePayload = {
  jti: string;
  label: string;
  iat: number;
  exp: number;
};

function b64urlEncode(data: Uint8Array | string): string {
  const bytes =
    typeof data === "string" ? new TextEncoder().encode(data) : data;
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]!);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(text: string): Uint8Array {
  const pad = "=".repeat((4 - (text.length % 4)) % 4);
  const b64 = (text + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmacSha256(secret: string, message: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(message),
  );
  return new Uint8Array(sig);
}

function stableJson(obj: InvitePayload): string {
  return JSON.stringify({
    exp: obj.exp,
    iat: obj.iat,
    jti: obj.jti,
    label: obj.label,
  });
}

export async function signInvite(
  secret: string,
  payload: InvitePayload,
): Promise<string> {
  const payloadB64 = b64urlEncode(stableJson(payload));
  const sig = b64urlEncode(await hmacSha256(secret, payloadB64));
  return `${payloadB64}.${sig}`;
}

export async function verifyInvite(
  secret: string,
  token: string,
  nowSec: number = Math.floor(Date.now() / 1000),
): Promise<InvitePayload> {
  if (!secret) throw new Error("secret required");
  if (!token || !token.includes(".")) throw new Error("malformed token");
  const idx = token.lastIndexOf(".");
  const payloadB64 = token.slice(0, idx);
  const sig = token.slice(idx + 1);
  const expected = b64urlEncode(await hmacSha256(secret, payloadB64));
  if (expected.length !== sig.length) throw new Error("bad signature");
  let ok = 0;
  for (let i = 0; i < expected.length; i++) {
    ok |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  }
  if (ok !== 0) throw new Error("bad signature");

  const raw = new TextDecoder().decode(b64urlDecode(payloadB64));
  const obj = JSON.parse(raw) as InvitePayload;
  if (!obj?.jti || typeof obj.exp !== "number") throw new Error("bad payload");
  if (nowSec >= obj.exp) throw new Error("expired");
  return obj;
}

export function randomJti(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return b64urlEncode(bytes);
}
