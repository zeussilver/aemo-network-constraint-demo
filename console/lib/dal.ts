import "server-only";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import { auth } from "./auth";
import { isRole } from "./rbac";
import type { Actor } from "./services/types";

/**
 * Data Access Layer entry point: the only place that turns a request into an Actor.
 * Cached per request, so a page and its components share one session lookup.
 */
export const getActor = cache(async (): Promise<Actor | null> => {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) return null;
  const role = (session.user as { role?: unknown }).role;
  return {
    id: session.user.id,
    email: session.user.email,
    name: session.user.name,
    // An unknown role is treated as viewer-with-nothing: can() grants it no permission.
    role: isRole(role) ? role : "none",
  };
});

/** For pages: redirect to the login page when there is no session. */
export async function requireActor(): Promise<Actor> {
  const actor = await getActor();
  if (!actor) redirect("/login");
  return actor;
}
