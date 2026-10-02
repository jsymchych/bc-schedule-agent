"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

const MESSAGES: Record<string, string> = {
  invite_required:
    "This hosted walkthrough opens with a single-use invite link from the operator. Google sign-in is not used here.",
  already_used:
    "That invite link was already used. Ask the operator for a fresh signed invite.",
  expired: "That invite link has expired. Ask the operator for a fresh signed invite.",
  invalid: "That invite link is invalid. Ask the operator for a fresh signed invite.",
  misconfigured:
    "Invite gate is misconfigured (signing secret or redeem ledger). Operator must fix env before demos.",
};

function LoginInner() {
  const searchParams = useSearchParams();
  const reason = searchParams.get("reason") || "invite_required";
  const message = MESSAGES[reason] || MESSAGES.invite_required;

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "2rem",
        background:
          "radial-gradient(ellipse at 20% 0%, #1a2e22 0%, #0f1410 55%, #0a0d0b 100%)",
      }}
    >
      <div style={{ width: "100%", maxWidth: "26rem" }}>
        <p
          style={{
            margin: "0 0 0.5rem",
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            fontSize: "0.7rem",
            color: "#8fa894",
          }}
        >
          kitchenstack-ai.com
        </p>
        <h1
          style={{
            margin: "0 0 0.75rem",
            fontSize: "1.85rem",
            fontWeight: 700,
            letterSpacing: "-0.02em",
          }}
        >
          Schedule demo
        </h1>
        <p style={{ margin: "0 0 1.25rem", color: "#b7c4b8", lineHeight: 1.5 }}>
          {message}
        </p>
        <p style={{ margin: 0, color: "#8fa894", fontSize: "0.9rem", lineHeight: 1.5 }}>
          This is not the live KitchenStack Scheduler. Open the invite URL you were
          sent (one redeem, then a short session).
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main style={{ minHeight: "100vh" }} />}>
      <LoginInner />
    </Suspense>
  );
}
