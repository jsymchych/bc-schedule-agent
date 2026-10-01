import { GoogleAuth, IdTokenClient } from "google-auth-library";

let cachedClient: IdTokenClient | null = null;
let cachedAudience: string | null = null;

/**
 * Mint a Cloud Run ID token using optional GCP_SA_JSON.
 * Without credentials, returns null — anonymous Cloud Run stays 403 (Wave A).
 */
export async function getCloudRunIdToken(
  audience: string,
): Promise<string | null> {
  const saJson = (process.env.GCP_SA_JSON ?? "").trim();
  if (!saJson) {
    return null;
  }

  if (cachedClient && cachedAudience === audience) {
    const headers = await cachedClient.getRequestHeaders();
    const auth = headers["Authorization"] ?? headers["authorization"];
    if (typeof auth === "string" && auth.startsWith("Bearer ")) {
      return auth.slice("Bearer ".length);
    }
  }

  const credentials = JSON.parse(saJson) as Record<string, unknown>;
  const auth = new GoogleAuth({ credentials });
  const client = await auth.getIdTokenClient(audience);
  cachedClient = client;
  cachedAudience = audience;
  const headers = await client.getRequestHeaders();
  const authHeader = headers["Authorization"] ?? headers["authorization"];
  if (typeof authHeader === "string" && authHeader.startsWith("Bearer ")) {
    return authHeader.slice("Bearer ".length);
  }
  return null;
}
