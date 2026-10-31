import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Card, Forbidden, Notice, PageHeader } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { formatNemTime } from "@/lib/format";
import { can } from "@/lib/rbac";
import { isSandbox } from "@/lib/sandbox";
import { getTraining } from "@/lib/services/training";
import { publicTask } from "@/lib/training";
import { checkTrainingAction } from "./actions";
import { TrainingTaskForm } from "./task-form";

export const metadata: Metadata = { title: "Training" };

export default async function TrainingPage() {
  if (!isSandbox()) notFound();
  const actor = await requireActor();
  if (!can(actor.role, "ops:read")) return <Forbidden what="training" />;
  const training = await getTraining(db, actor);
  if (!training) {
    return (
      <>
        <PageHeader title="Training" />
        <Notice>No simulated world is loaded yet. Run the simulator&apos;s load command for this sandbox.</Notice>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Training"
        subtitle={`${training.title}, loaded ${formatNemTime(training.anchor)}. Find each answer in the console's own pages, then check it here.`}
      />
      <ol className="flex max-w-3xl flex-col gap-4">
        {training.tasks.map((task, index) => (
          <li key={task.id}>
            <Card title={`Task ${index + 1} · ${task.title}`}>
              <TrainingTaskForm task={publicTask(task)} sites={training.sites} action={checkTrainingAction} />
            </Card>
          </li>
        ))}
      </ol>
      <details className="mt-6 max-w-3xl text-sm text-slate-700">
        <summary className="cursor-pointer font-medium">What the simulator scripted (spoilers)</summary>
        <ul className="mt-2 flex flex-col gap-2">
          {training.scenarios.map((scenario) => (
            <li key={scenario.key}>
              <span className="font-medium">{scenario.title}.</span> {scenario.description}
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}
