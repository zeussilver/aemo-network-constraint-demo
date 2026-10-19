import { z } from "zod";
import { ForbiddenError } from "./rbac";
import { ConflictError, NotFoundError, RuleError } from "./services/types";

export type FieldErrors = Record<string, string[] | undefined>;

export type ActionResult =
  | { ok: true; message?: string }
  | { ok: false; error: string; fieldErrors?: FieldErrors; values?: Record<string, string> };

export const idle: ActionResult = { ok: true };

/**
 * Map a thrown error to something a form can show. Known errors become messages; anything
 * else is logged on the server and reported generically, so no internals reach the browser.
 */
export function toActionError(error: unknown, values?: Record<string, string>): ActionResult {
  if (error instanceof z.ZodError) {
    return { ok: false, error: "Check the highlighted fields.", fieldErrors: z.flattenError(error).fieldErrors, values };
  }
  if (error instanceof ForbiddenError) {
    return { ok: false, error: "Your role does not allow this change.", values };
  }
  if (error instanceof ConflictError || error instanceof RuleError || error instanceof NotFoundError) {
    return { ok: false, error: error.message, values };
  }
  console.error("Unexpected server action error", error);
  return { ok: false, error: "Something went wrong. The change was not saved.", values };
}
