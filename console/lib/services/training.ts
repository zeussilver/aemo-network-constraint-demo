import { parseManifest } from "../training";
import { assertCan } from "./audit";
import type { Actor, Db } from "./types";

/** The latest sandbox load and its tasks (with answers: server-side only). */
export async function getTraining(db: Db, actor: Actor) {
  assertCan(actor, "ops:read");
  const run = await db.simRun.findFirst({ orderBy: { id: "desc" } });
  if (!run) return null;
  const manifest = parseManifest(run.manifest);
  return {
    id: run.id,
    title: manifest.title,
    anchor: run.anchor,
    referenceDay: manifest.reference_day,
    sites: manifest.sites.map(({ key, name }) => ({ key, name })),
    scenarios: manifest.scenarios,
    tasks: manifest.tasks,
  };
}
