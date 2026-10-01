import { NextResponse } from "next/server";

/** Edge control-plane probe — not proxied to Cloud Run. */
export async function GET() {
  return NextResponse.json({
    ok: true,
    service: "bc-schedule-agent-edge",
    host: "schedule-demo.kitchenstack-ai.com",
  });
}
