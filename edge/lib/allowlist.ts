/**
 * ALLOWED_EMAILS-style gate for schedule-demo.
 * Fail-closed: empty / missing list admits nobody (invite story).
 * Differs from KS_AI app habit (empty = allow any Google) on purpose.
 */

export function parseAllowedEmails(raw: string | undefined | null): string[] {
  return (raw ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
}

export function isEmailAllowed(
  email: string | undefined | null,
  allowed: readonly string[],
): boolean {
  if (!email) return false;
  if (allowed.length === 0) return false;
  return allowed.includes(email.trim().toLowerCase());
}
