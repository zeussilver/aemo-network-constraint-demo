import "server-only";
import { betterAuth } from "better-auth";
import { prismaAdapter } from "better-auth/adapters/prisma";
import { nextCookies } from "better-auth/next-js";
import { authOptions } from "./auth-options";
import { db } from "./db";

export const auth = betterAuth({
  ...authOptions,
  database: prismaAdapter(db, { provider: "postgresql" }),
  // Must stay last: lets server actions (sign in / sign out) set the session cookie.
  plugins: [nextCookies()],
});
