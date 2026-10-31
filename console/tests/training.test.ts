import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { ForbiddenError } from "@/lib/rbac";
import { getTraining } from "@/lib/services/training";
import { checkAnswer, describeAnswer, parseManifest, publicTask, type TrainingTask } from "@/lib/training";
import { hasTestDb, nobody, testDb, viewer } from "./db-helpers";

const curtailment: TrainingTask = {
  id: "network-constraint",
  title: "A network constraint holds a wind farm back",
  kind: "largest_curtailment",
  prompt: "Which site had the largest curtailment estimate?",
  fields: [
    { name: "site", label: "Site", type: "site" },
    { name: "mwh", label: "Curtailment estimate (MWh)", type: "number", tolerance: 93.1 },
  ],
  answer: { site: "wind-a", mwh: 931.1 },
  explanation: "Shaded intervals on the site page.",
};

const outage: TrainingTask = {
  ...curtailment,
  id: "scada-outage",
  kind: "scada_outage",
  fields: [
    { name: "site", label: "Site", type: "site" },
    { name: "time", label: "Time of its last reading", type: "time", tolerance_minutes: 5 },
  ],
  answer: { site: "solar-b", time: "23:58" },
};

const ingest: TrainingTask = {
  ...curtailment,
  id: "ingest-failure",
  kind: "ingest_failure",
  fields: [{ name: "source", label: "Source", type: "choice", options: ["scada", "dispatch", "intermittent", "forecast"] }],
  answer: { source: "scada" },
};

describe("checkAnswer", () => {
  it("accepts a number inside the tolerance and names the fields that are wrong", () => {
    expect(checkAnswer(curtailment, { site: "wind-a", mwh: "900" })).toEqual({ correct: true, wrongFields: [] });
    expect(checkAnswer(curtailment, { site: "wind-a", mwh: "800" })).toEqual({ correct: false, wrongFields: ["Curtailment estimate (MWh)"] });
    expect(checkAnswer(curtailment, { site: "solar-b", mwh: "931.1" }).wrongFields).toEqual(["Site"]);
  });

  it("treats blanks and non-numbers as wrong, never as zero", () => {
    expect(checkAnswer(curtailment, { site: "", mwh: "" }).wrongFields).toHaveLength(2);
    expect(checkAnswer(curtailment, { site: "wind-a", mwh: "lots" }).correct).toBe(false);
  });

  it("compares times on a 24-hour clock, across midnight", () => {
    expect(checkAnswer(outage, { site: "solar-b", time: "00:02" }).correct).toBe(true); // 4 minutes after 23:58
    expect(checkAnswer(outage, { site: "solar-b", time: "23:50" }).correct).toBe(false);
    expect(checkAnswer(outage, { site: "solar-b", time: "24:00" }).correct).toBe(false);
    expect(checkAnswer(outage, { site: "solar-b", time: "noon" }).correct).toBe(false);
  });

  it("matches choices exactly", () => {
    expect(checkAnswer(ingest, { source: "scada" }).correct).toBe(true);
    expect(checkAnswer(ingest, { source: "SCADA" }).correct).toBe(false);
  });
});

describe("what reaches the browser", () => {
  it("strips the answer and the explanation", () => {
    const visible = publicTask(curtailment);
    expect(visible).not.toHaveProperty("answer");
    expect(visible).not.toHaveProperty("explanation");
    expect(JSON.stringify(visible)).not.toContain("931.1");
  });

  it("describes a revealed answer with site names, not keys", () => {
    expect(describeAnswer(curtailment, [{ key: "wind-a", name: "Sim Wind Farm A" }])).toEqual([
      ["Site", "Sim Wind Farm A"],
      ["Curtailment estimate (MWh)", "931.1 (±93.1 accepted)"],
    ]);
  });

  it("rejects a malformed manifest", () => {
    expect(() => parseManifest({ world: "training" })).toThrow();
    expect(() =>
      parseManifest({
        world: "w",
        title: "t",
        anchor: "a",
        reference_day: "d",
        sites: [],
        scenarios: [],
        tasks: [{ ...curtailment, fields: [{ name: "x", label: "X", type: "slider" }] }],
      }),
    ).toThrow();
  });
});

describe.skipIf(!hasTestDb)("getTraining (Postgres)", () => {
  const db = testDb();
  afterAll(() => db.$disconnect());
  beforeEach(() => db.$executeRawUnsafe('TRUNCATE TABLE "sim_run" RESTART IDENTITY'));

  const manifest = (title: string) => ({
    world: "training",
    title,
    anchor: "2026-11-03T14:37:00+10:00",
    reference_day: "2026-11-02",
    sites: [{ key: "wind-a", duid: "SIMWDA1", name: "Sim Wind Farm A" }],
    scenarios: [{ key: "network-constraint", title: "t", description: "d" }],
    tasks: [curtailment],
  });

  it("returns nothing before the simulator has loaded a world", async () => {
    expect(await getTraining(db, viewer)).toBeNull();
  });

  it("reads the latest load and checks the role", async () => {
    const anchor = new Date("2026-11-03T04:37:00Z");
    const referenceDay = new Date("2026-11-02T00:00:00Z");
    await db.simRun.create({ data: { world: "training", seed: 1, anchor, referenceDay, manifest: manifest("Older") } });
    await db.simRun.create({ data: { world: "training", seed: 1, anchor, referenceDay, manifest: manifest("Newer") } });
    const training = await getTraining(db, viewer);
    expect(training?.title).toBe("Newer");
    expect(training?.sites).toEqual([{ key: "wind-a", name: "Sim Wind Farm A" }]);
    expect(training?.tasks[0].answer).toEqual({ site: "wind-a", mwh: 931.1 });
    await expect(getTraining(db, nobody)).rejects.toBeInstanceOf(ForbiddenError);
  });
});
