"use client";

import { useActionState } from "react";
import { fieldError, FormMessage, lastValue, SubmitButton } from "@/components/form-status";
import { Field, Input, LinkButton, Select, Textarea } from "@/components/ui";
import { idle, type ActionResult } from "@/lib/action-result";

type Customer = { id: string; version: number; name: string; contactEmail: string; status: string; notes: string | null };

export function CustomerForm({
  action,
  customer,
  cancelHref,
}: {
  action: (prev: ActionResult, formData: FormData) => Promise<ActionResult>;
  customer?: Customer;
  cancelHref: string;
}) {
  const [state, formAction] = useActionState(action, idle);
  return (
    <form action={formAction} className="flex max-w-xl flex-col gap-4" noValidate>
      <FormMessage state={state} />
      {customer && (
        <>
          <input type="hidden" name="id" value={customer.id} />
          <input type="hidden" name="version" value={customer.version} />
        </>
      )}
      <Field label="Name" name="name" error={fieldError(state, "name")}>
        <Input id="name" name="name" defaultValue={lastValue(state, "name", customer?.name)} aria-invalid={!!fieldError(state, "name")} />
      </Field>
      <Field label="Operations contact email" name="contactEmail" error={fieldError(state, "contactEmail")} hint="Demo data: use an example.com address.">
        <Input
          id="contactEmail"
          name="contactEmail"
          type="email"
          defaultValue={lastValue(state, "contactEmail", customer?.contactEmail)}
          aria-invalid={!!fieldError(state, "contactEmail")}
        />
      </Field>
      <Field label="Status" name="status" error={fieldError(state, "status")}>
        <Select id="status" name="status" defaultValue={lastValue(state, "status", customer?.status ?? "ONBOARDING")}>
          <option value="ONBOARDING">Onboarding</option>
          <option value="ACTIVE">Active</option>
          <option value="INACTIVE">Inactive</option>
        </Select>
      </Field>
      <Field label="Notes" name="notes" error={fieldError(state, "notes")}>
        <Textarea id="notes" name="notes" rows={3} defaultValue={lastValue(state, "notes", customer?.notes)} />
      </Field>
      <div className="flex gap-2">
        <SubmitButton>{customer ? "Save changes" : "Create customer"}</SubmitButton>
        <LinkButton href={cancelHref}>Cancel</LinkButton>
      </div>
    </form>
  );
}
