/**
 * Atomic single-use redeem ledger (2B).
 *
 * Backends (first match wins):
 * 1. Upstash / Vercel KV REST — KV_REST_API_URL + KV_REST_API_TOKEN
 * 2. Vercel Blob — BLOB_STORE_ID / BLOB_READ_WRITE_TOKEN (OIDC on Vercel)
 * 3. GCS — INVITE_REDEEM_GCS_BUCKET + GCP_SA_JSON (ifGenerationMatch=0)
 * 4. memory — INVITE_REDEEM_BACKEND=memory or non-production without durable backend
 */

import { put } from "@vercel/blob";
import { GoogleAuth } from "google-auth-library";

const memory = new Map<string, string>();

export type ClaimResult = "claimed" | "already";

function keyFor(jti: string): string {
  return `invite:redeemed:${jti}`;
}

function objectName(jti: string): string {
  const safe = encodeURIComponent(jti);
  return `invite-redeemed/${safe}.json`;
}

async function claimViaKv(jti: string): Promise<ClaimResult> {
  const base = process.env.KV_REST_API_URL?.replace(/\/+$/, "");
  const token = process.env.KV_REST_API_TOKEN?.trim();
  if (!base || !token) {
    throw new Error("KV_REST_API_URL / KV_REST_API_TOKEN required");
  }
  const value = JSON.stringify({ at: Date.now(), jti });
  const res = await fetch(base, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(["SET", keyFor(jti), value, "NX"]),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`redeem ledger HTTP ${res.status}`);
  }
  const data = (await res.json()) as { result: string | null };
  if (data.result === "OK") return "claimed";
  return "already";
}

async function claimViaBlob(jti: string): Promise<ClaimResult> {
  const pathname = objectName(jti);
  const body = JSON.stringify({ at: Date.now(), jti });
  try {
    await put(pathname, body, {
      access: "private",
      addRandomSuffix: false,
      allowOverwrite: false,
      contentType: "application/json",
    });
    return "claimed";
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    // Already exists / conflict → single-use burn already happened
    if (
      /already exists/i.test(msg) ||
      /overwrite/i.test(msg) ||
      /409/.test(msg) ||
      /412/.test(msg)
    ) {
      return "already";
    }
    throw err;
  }
}

async function gcsAccessToken(): Promise<string> {
  const saJson = (process.env.GCP_SA_JSON ?? "").trim();
  if (!saJson) throw new Error("GCP_SA_JSON required for GCS redeem");
  const credentials = JSON.parse(saJson) as Record<string, unknown>;
  const auth = new GoogleAuth({
    credentials,
    scopes: ["https://www.googleapis.com/auth/devstorage.read_write"],
  });
  const client = await auth.getClient();
  const token = await client.getAccessToken();
  if (!token.token) throw new Error("GCS access token unavailable");
  return token.token;
}

async function claimViaGcs(jti: string): Promise<ClaimResult> {
  const bucket = (process.env.INVITE_REDEEM_GCS_BUCKET ?? "").trim();
  if (!bucket) throw new Error("INVITE_REDEEM_GCS_BUCKET required");
  const token = await gcsAccessToken();
  const name = objectName(jti);
  const url = `https://storage.googleapis.com/upload/storage/v1/b/${encodeURIComponent(bucket)}/o?uploadType=media&name=${encodeURIComponent(name)}&ifGenerationMatch=0`;
  const body = JSON.stringify({ at: Date.now(), jti });
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body,
    cache: "no-store",
  });
  if (res.status === 200 || res.status === 201) return "claimed";
  if (res.status === 412 || res.status === 409) return "already";
  const text = await res.text();
  throw new Error(`GCS redeem HTTP ${res.status}: ${text.slice(0, 200)}`);
}

function claimViaMemory(jti: string): ClaimResult {
  const k = keyFor(jti);
  if (memory.has(k)) return "already";
  memory.set(k, String(Date.now()));
  return "claimed";
}

/** Test helper — clear in-memory ledger. */
export function resetMemoryRedeemLedger(): void {
  memory.clear();
}

function hasBlobBackend(): boolean {
  return Boolean(
    process.env.BLOB_STORE_ID?.trim() ||
      process.env.BLOB_READ_WRITE_TOKEN?.trim(),
  );
}

export async function claimInviteJti(jti: string): Promise<ClaimResult> {
  if (!jti) throw new Error("jti required");
  const backend = (process.env.INVITE_REDEEM_BACKEND || "").trim().toLowerCase();
  if (backend === "memory") return claimViaMemory(jti);
  if (backend === "blob") return claimViaBlob(jti);
  if (backend === "gcs") return claimViaGcs(jti);
  if (backend === "kv") return claimViaKv(jti);

  if (process.env.KV_REST_API_URL && process.env.KV_REST_API_TOKEN) {
    return claimViaKv(jti);
  }
  if (hasBlobBackend()) {
    return claimViaBlob(jti);
  }
  if (
    process.env.INVITE_REDEEM_GCS_BUCKET?.trim() &&
    process.env.GCP_SA_JSON?.trim()
  ) {
    return claimViaGcs(jti);
  }
  if (process.env.NODE_ENV === "production") {
    throw new Error(
      "Redeem ledger not configured — set Blob (BLOB_STORE_ID), KV_REST_API_*, or GCS bucket",
    );
  }
  return claimViaMemory(jti);
}
