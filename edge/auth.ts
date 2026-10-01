import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import type { NextRequest } from "next/server";
import { isEmailAllowed, parseAllowedEmails } from "@/lib/allowlist";

const ALLOWED_EMAILS = parseAllowedEmails(process.env.ALLOWED_EMAILS);

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 12 * 60 * 60, // 12h — shared demo; short session
  },
  pages: {
    signIn: "/login",
    error: "/login",
  },
  callbacks: {
    authorized({ auth: session, request }: { auth: any; request: NextRequest }) {
      const path = request.nextUrl.pathname;
      if (
        path === "/login" ||
        path.startsWith("/api/auth") ||
        path === "/api/health"
      ) {
        return true;
      }

      const isAuthenticated = !!session?.user;
      if (isAuthenticated) return true;

      if (path.startsWith("/api")) {
        return Response.json({ error: "Unauthorized" }, { status: 401 });
      }

      const loginUrl = new URL("/login", request.nextUrl.origin);
      loginUrl.searchParams.set(
        "callbackUrl",
        `${request.nextUrl.pathname}${request.nextUrl.search}`,
      );
      return Response.redirect(loginUrl);
    },
    async signIn({ user }) {
      // Fail-closed: unlisted (and empty allowlist) never reach demo UI.
      return isEmailAllowed(user.email, ALLOWED_EMAILS);
    },
  },
});
