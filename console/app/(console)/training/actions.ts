"use server";

import { getActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { isSandbox } from "@/lib/sandbox";
import { getTraining } from "@/lib/services/training";
import { checkAnswer, describeAnswer } from "@/lib/training";

/** `values` echoes what was submitted, so the form keeps it after React resets the form. */
export type TrainingState =
  | { status: "idle" }
  | { status: "checked"; correct: boolean; wrongFields: string[]; explanation?: string; values: Record<string, string> }
  | { status: "revealed"; answer: Array<[string, string]>; explanation: string; values: Record<string, string> }
  | { status: "error"; message: string };

export async function checkTrainingAction(_prev: TrainingState, formData: FormData): Promise<TrainingState> {
  const actor = await getActor();
  if (!actor) return { status: "error", message: "Your session has ended. Sign in again." };
  if (!isSandbox()) return { status: "error", message: "Training runs only in the sandbox." };
  const training = await getTraining(db, actor);
  const task = training?.tasks.find((item) => item.id === formData.get("taskId"));
  if (!training || !task) return { status: "error", message: "This task is no longer loaded. Reload the page." };

  const values = Object.fromEntries(task.fields.map((field) => [field.name, String(formData.get(field.name) ?? "")]));
  if (formData.get("intent") === "reveal") {
    return { status: "revealed", answer: describeAnswer(task, training.sites), explanation: task.explanation, values };
  }
  const result = checkAnswer(task, values);
  return { status: "checked", ...result, explanation: result.correct ? task.explanation : undefined, values };
}
