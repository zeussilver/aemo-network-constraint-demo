"use server";

import { APIError } from "better-auth/api";
import { redirect } from "next/navigation";
import { z } from "zod";
import type { ActionResult } from "@/lib/action-result";
import { auth } from "@/lib/auth";

const credentials = z.object({
  email: z.string().trim().toLowerCase().pipe(z.email()),
  password: z.string().min(1),
});

export async function signIn(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const parsed = credentials.safeParse({ email: formData.get("email"), password: formData.get("password") });
  const email = String(formData.get("email") ?? "");
  if (!parsed.success) {
    return { ok: false, error: "Enter your email and password.", values: { email } };
  }
  try {
    // nextCookies() (lib/auth.ts) writes the session cookie from this server action.
    await auth.api.signInEmail({ body: parsed.data });
  } catch (error) {
    if (error instanceof APIError) {
      // Same message for unknown email and wrong password: no account enumeration.
      return { ok: false, error: "Email or password is incorrect.", values: { email } };
    }
    throw error;
  }
  redirect("/");
}
