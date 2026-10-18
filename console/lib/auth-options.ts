import type { BetterAuthOptions } from "better-auth";
import { ROLES } from "./rbac";

/**
 * Better Auth options shared by the app (lib/auth.ts) and the schema generator.
 *
 * - No public sign-up: staff accounts are created by the seed script or an admin.
 * - `role` is server-owned (`input: false`), so a user cannot grant themselves admin.
 * - Sessions last 8 hours, roughly one working shift.
 */
export const authOptions = {
  emailAndPassword: {
    enabled: true,
    disableSignUp: true,
    minPasswordLength: 12,
  },
  user: {
    additionalFields: {
      role: {
        type: [...ROLES],
        required: true,
        defaultValue: "viewer",
        input: false,
      },
    },
  },
  session: {
    expiresIn: 60 * 60 * 8,
    updateAge: 60 * 60,
  },
  telemetry: { enabled: false },
} satisfies Partial<BetterAuthOptions>;
