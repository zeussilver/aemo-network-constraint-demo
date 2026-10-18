"use client";

import { useActionState } from "react";
import { fieldError, FormMessage, lastValue, SubmitButton } from "@/components/form-status";
import { Field, Select } from "@/components/ui";
import { idle } from "@/lib/action-result";
import { assignUnitAction, removeUnitAction } from "./actions";

/**
 * Always rendered, even when no DUID is left to assign, so the result message of the last
 * assignment stays on screen after the list empties.
 */
export function AssignUnitForm({
  siteId,
  options,
  emptyText,
}: {
  siteId: string;
  options: Array<{ duid: string; stationName: string; region: string; fuel: string }>;
  emptyText: string;
}) {
  const [state, formAction] = useActionState(assignUnitAction, idle);
  return (
    <form action={formAction} className="flex flex-col gap-2">
      <input type="hidden" name="siteId" value={siteId} />
      {options.length > 0 ? (
        <>
          <Field label="Add a DUID from the AEMO registry" name="duid" error={fieldError(state, "duid")}>
            <Select id="duid" name="duid" defaultValue={lastValue(state, "duid", "")}>
              <option value="">Choose an unassigned DUID</option>
              {options.map((option) => (
                <option key={option.duid} value={option.duid}>
                  {option.duid} · {option.stationName} ({option.region}, {option.fuel.toLowerCase()})
                </option>
              ))}
            </Select>
          </Field>
          <div>
            <SubmitButton tone="secondary">Assign unit</SubmitButton>
          </div>
        </>
      ) : (
        <p className="text-sm text-slate-500">{emptyText}</p>
      )}
      <FormMessage state={state} />
    </form>
  );
}

export function RemoveUnitButton({ duid }: { duid: string }) {
  const [state, formAction] = useActionState(removeUnitAction, idle);
  return (
    <form
      action={formAction}
      onSubmit={(event) => {
        if (!window.confirm(`Remove ${duid} from this site?`)) event.preventDefault();
      }}
    >
      <input type="hidden" name="duid" value={duid} />
      <SubmitButton tone="danger">Remove</SubmitButton>
      {!state.ok && <p className="mt-1 text-xs text-red-700">{state.error}</p>}
    </form>
  );
}
