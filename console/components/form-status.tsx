"use client";

import { useFormStatus } from "react-dom";
import type { ActionResult } from "@/lib/action-result";
import { Button, Notice } from "./ui";

export function SubmitButton({ children, tone }: { children: React.ReactNode; tone?: "primary" | "secondary" | "danger" }) {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" tone={tone} disabled={pending}>
      {pending ? "Saving…" : children}
    </Button>
  );
}

export function FormMessage({ state }: { state: ActionResult }) {
  if (state.ok) {
    return state.message ? <Notice tone="green">{state.message}</Notice> : null;
  }
  return <Notice tone="red">{state.error}</Notice>;
}

/** Field errors for one input, or undefined when the last submit had none. */
export function fieldError(state: ActionResult, name: string): string[] | undefined {
  return state.ok ? undefined : state.fieldErrors?.[name];
}

/** The value the user last submitted (so a failed form keeps its input), else the default. */
export function lastValue(state: ActionResult, name: string, fallback: string | number | null | undefined): string {
  if (!state.ok && state.values && name in state.values) return state.values[name];
  return fallback == null ? "" : String(fallback);
}
