"use client";

import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function LoginInner() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  const error = searchParams.get("error");

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
        <p style={{ margin: "0 0 1.75rem", color: "#b7c4b8", lineHeight: 1.5 }}>
          Sign in with an invited Google account to open the hosted walkthrough.
          This is not the live KitchenStack Scheduler.
        </p>

        {error ? (
          <div
            role="alert"
            style={{
              marginBottom: "1.25rem",
              padding: "0.85rem 1rem",
              borderRadius: "0.5rem",
              background: "rgba(180, 60, 50, 0.18)",
              border: "1px solid rgba(220, 120, 100, 0.35)",
              color: "#f0c4bc",
              fontSize: "0.9rem",
            }}
          >
            {error === "AccessDenied"
              ? "That Google account is not on the schedule-demo allowlist."
              : "Sign-in failed. Try again or ask the operator to confirm your invite."}
          </div>
        ) : null}

        <button
          type="button"
          onClick={() => signIn("google", { callbackUrl })}
          style={{
            width: "100%",
            padding: "0.9rem 1.1rem",
            borderRadius: "0.55rem",
            border: "1px solid #3d5244",
            background: "#e8efe6",
            color: "#121814",
            fontWeight: 650,
            fontSize: "0.95rem",
            cursor: "pointer",
          }}
        >
          Sign in with Google
        </button>
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
