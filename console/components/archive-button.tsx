"use client";

import { useActionState } from "react";
import type { ActionResult } from "@/lib/action-result";
import { idle } from "@/lib/action-result";
import { FormMessage, SubmitButton } from "./form-status";

/** Archive / restore toggle with a confirm step. Archiving is reversible, so it is not a delete. */
export function ArchiveButton({
  action,
  id,
  version,
  archived,
  noun,
}: {
  action: (prev: ActionResult, formData: FormData) => Promise<ActionResult>;
  id: string;
  version: number;
  archived: boolean;
  noun: string;
}) {
  const [state, formAction] = useActionState(action, idle);
  return (
    <form
      action={formAction}
      onSubmit={(event) => {
        if (!archived && !window.confirm(`Archive this ${noun}? It becomes read-only until restored.`)) {
          event.preventDefault();
        }
      }}
      className="flex flex-col items-end gap-2"
    >
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="version" value={version} />
      <input type="hidden" name="archive" value={archived ? "0" : "1"} />
      <SubmitButton tone={archived ? "secondary" : "danger"}>{archived ? `Restore ${noun}` : `Archive ${noun}`}</SubmitButton>
      <FormMessage state={state} />
    </form>
  );
}
