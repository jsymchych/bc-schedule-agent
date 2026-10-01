export { auth as default } from "@/auth";

export const config = {
  matcher: [
    /*
     * Protect all routes except Next internals + static favicon.
     * /login and /api/auth are allowlisted inside authorized().
     */
    "/((?!_next/static|_next/image|favicon\\.ico).*)",
  ],
};
