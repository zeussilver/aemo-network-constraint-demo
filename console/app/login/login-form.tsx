"use client";

import { useActionState } from "react";
import { FormMessage, lastValue, SubmitButton } from "@/components/form-status";
import { Field, Input } from "@/components/ui";
import { idle } from "@/lib/action-result";
import { signIn } from "./actions";

export function LoginForm() {
  const [state, formAction] = useActionState(signIn, idle);
  return (
    <form action={formAction} className="flex flex-col gap-4">
      <FormMessage state={state} />
      <Field label="Email" name="email">
        <Input id="email" name="email" type="email" autoComplete="username" required defaultValue={lastValue(state, "email", "")} />
      </Field>
      <Field label="Password" name="password">
        <Input id="password" name="password" type="password" autoComplete="current-password" required />
      </Field>
      <SubmitButton>Sign in</SubmitButton>
    </form>
  );
}
