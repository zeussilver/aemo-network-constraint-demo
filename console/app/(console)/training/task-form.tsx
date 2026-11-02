"use client";

import { useActionState } from "react";
import { Button, DefinitionList, Field, Input, Notice, Select } from "@/components/ui";
import type { PublicTask, SiteOption } from "@/lib/training";
import type { TrainingState } from "./actions";

export function TrainingTaskForm({
  task,
  sites,
  action,
}: {
  task: PublicTask;
  sites: SiteOption[];
  action: (prev: TrainingState, formData: FormData) => Promise<TrainingState>;
}) {
  const [state, formAction, pending] = useActionState(action, { status: "idle" } as TrainingState);
  const id = (name: string) => `${task.id}-${name}`;
  const last = (name: string) => ("values" in state ? state.values[name] : undefined) ?? "";
  return (
    <form action={formAction} className="flex flex-col gap-3" aria-label={task.title}>
      <input type="hidden" name="taskId" value={task.id} />
      <p className="text-sm text-slate-800">{task.prompt}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {task.fields.map((field) => (
          <Field key={field.name} label={field.label} name={id(field.name)}>
            {field.type === "site" || field.type === "choice" ? (
              // Keyed on the submitted value: a select only keeps a new defaultValue across
              // React's post-action form reset if it mounts with it.
              <Select key={last(field.name)} id={id(field.name)} name={field.name} defaultValue={last(field.name)}>
                <option value="" disabled>
                  Choose…
                </option>
                {field.type === "site"
                  ? sites.map((site) => (
                      <option key={site.key} value={site.key}>
                        {site.name}
                      </option>
                    ))
                  : field.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
              </Select>
            ) : (
              <Input
                id={id(field.name)}
                name={field.name}
                defaultValue={last(field.name)}
                inputMode={field.type === "number" ? "decimal" : undefined}
                placeholder={field.type === "time" ? "HH:MM" : undefined}
              />
            )}
          </Field>
        ))}
      </div>
      <div className="flex gap-2">
        <Button type="submit" name="intent" value="check" disabled={pending}>
          Check answer
        </Button>
        <Button type="submit" name="intent" value="reveal" tone="secondary" disabled={pending}>
          Show the answer
        </Button>
      </div>
      {state.status === "error" && <Notice tone="red">{state.message}</Notice>}
      {state.status === "checked" &&
        (state.correct ? (
          <Notice tone="green">
            <p className="font-semibold">Correct.</p>
            <p className="mt-1">{state.explanation}</p>
          </Notice>
        ) : (
          <Notice tone="amber">Not yet. Look again at: {state.wrongFields.join("; ")}.</Notice>
        ))}
      {state.status === "revealed" && (
        <Notice tone="slate">
          <DefinitionList items={state.answer} />
          <p className="mt-2">{state.explanation}</p>
        </Notice>
      )}
    </form>
  );
}
