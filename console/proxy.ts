import { getSessionCookie } from "better-auth/cookies";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Optimistic gate only: it checks that a session cookie exists, not that it is valid.
 * Every page and server action validates the session and role itself (lib/dal.ts and
 * the services), so a forged cookie gets past this redirect but nothing further.
 */
export function proxy(request: NextRequest) {
  if (!getSessionCookie(request)) {
    const login = new URL("/login", request.url);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  // Everything except sign-in, the auth API, health probes and static assets.
  matcher: ["/((?!login|api/auth|api/health|api/ready|_next/static|_next/image|favicon.ico).*)"],
};
