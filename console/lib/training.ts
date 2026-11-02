import { z } from "zod";

/**
 * Training tasks for the sandbox. The simulator stores each task in `sim_run.manifest`
 * with its answer read from the data it generated (sim_scenarios.build_tasks), so the
 * answer always matches what the console shows. Only prompts and fields reach the
 * browser; answers are checked on the server.
 */

const field = z.discriminatedUnion("type", [
  z.object({ name: z.string(), label: z.string(), type: z.literal("site") }),
  z.object({ name: z.string(), label: z.string(), type: z.literal("number"), tolerance: z.number().nonnegative() }),
  z.object({ name: z.string(), label: z.string(), type: z.literal("time"), tolerance_minutes: z.number().nonnegative() }),
  z.object({ name: z.string(), label: z.string(), type: z.literal("choice"), options: z.array(z.string()).min(2) }),
]);

const task = z.object({
  id: z.string(),
  title: z.string(),
  kind: z.string(),
  prompt: z.string(),
  fields: z.array(field).min(1),
  answer: z.record(z.string(), z.union([z.string(), z.number()])),
  explanation: z.string(),
});

const manifest = z.object({
  world: z.string(),
  title: z.string(),
  anchor: z.string(),
  reference_day: z.string(),
  sites: z.array(z.object({ key: z.string(), duid: z.string(), name: z.string() })),
  scenarios: z.array(z.object({ key: z.string(), title: z.string(), description: z.string() })),
  tasks: z.array(task),
});

export type TrainingField = z.infer<typeof field>;
export type TrainingTask = z.infer<typeof task>;
export type TrainingManifest = z.infer<typeof manifest>;
export type PublicTask = Omit<TrainingTask, "answer" | "explanation">;
export type SiteOption = { key: string; name: string };

export function parseManifest(value: unknown): TrainingManifest {
  return manifest.parse(value);
}

/** What the browser may see of a task. */
export function publicTask({ id, title, kind, prompt, fields }: TrainingTask): PublicTask {
  return { id, title, kind, prompt, fields };
}

function minutesOf(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const [hours, minutes] = [Number(match[1]), Number(match[2])];
  return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
}

function fieldCorrect(spec: TrainingField, submitted: string, expected: string | number): boolean {
  const value = submitted.trim();
  if (value === "") return false;
  switch (spec.type) {
    case "site":
    case "choice":
      return value === String(expected);
    case "number": {
      const number = Number(value);
      return Number.isFinite(number) && Math.abs(number - Number(expected)) <= spec.tolerance;
    }
    case "time": {
      const [given, wanted] = [minutesOf(value), minutesOf(String(expected))];
      if (given == null || wanted == null) return false;
      const difference = Math.abs(given - wanted);
      return Math.min(difference, 24 * 60 - difference) <= spec.tolerance_minutes;
    }
  }
}

export type CheckResult = { correct: boolean; wrongFields: string[] };

export function checkAnswer(trainingTask: TrainingTask, submitted: Record<string, string>): CheckResult {
  const wrongFields = trainingTask.fields
    .filter((spec) => !fieldCorrect(spec, submitted[spec.name] ?? "", trainingTask.answer[spec.name]))
    .map((spec) => spec.label);
  return { correct: wrongFields.length === 0, wrongFields };
}

/** The answer as label/value pairs for staff to read, with site keys shown as names. */
export function describeAnswer(trainingTask: TrainingTask, sites: SiteOption[]): Array<[string, string]> {
  return trainingTask.fields.map((spec) => {
    const value = trainingTask.answer[spec.name];
    if (spec.type === "site") return [spec.label, sites.find((site) => site.key === value)?.name ?? String(value)];
    if (spec.type === "number") return [spec.label, `${value} (±${spec.tolerance} accepted)`];
    if (spec.type === "time") return [spec.label, `${value} NEM time (±${spec.tolerance_minutes} min accepted)`];
    return [spec.label, String(value)];
  });
}
