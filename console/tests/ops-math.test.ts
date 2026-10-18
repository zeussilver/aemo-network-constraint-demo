import { describe, expect, it } from "vitest";
import { assessFreshness, originCategory, summariseCurtailment, tradingDayBounds, tradingDayOf } from "@/lib/ops-math";

const nem = (local: string) => new Date(`${local}+10:00`);

describe("trading days", () => {
  it("puts 04:05 in the new day and 04:00 in the previous one", () => {
    expect(tradingDayOf(nem("2026-10-17T04:05:00"))).toBe("2026-10-17");
    expect(tradingDayOf(nem("2026-10-18T04:00:00"))).toBe("2026-10-17");
    expect(tradingDayOf(nem("2026-10-18T00:00:00"))).toBe("2026-10-17");
    expect(tradingDayOf(nem("2026-10-18T04:05:00"))).toBe("2026-10-18");
  });

  it("bounds a trading day as (D 04:00, D+1 04:00]", () => {
    const { after, until } = tradingDayBounds("2026-10-17");
    expect(after.toISOString()).toBe("2026-10-16T18:00:00.000Z");
    expect(until.toISOString()).toBe("2026-10-17T18:00:00.000Z");
    expect(() => tradingDayBounds("17/10/2026")).toThrow();
  });
});

describe("summariseCurtailment", () => {
  it("counts only capped intervals and never goes negative", () => {
    const summary = summariseCurtailment([
      { totalCleared: 30, uigf: 42, semidispatchCap: 1 }, // 12 MW short for 5 min = 1 MWh
      { totalCleared: 50, uigf: 44, semidispatchCap: 1 }, // cleared above UIGF: 0
      { totalCleared: 20, uigf: 80, semidispatchCap: 0 }, // not capped: not curtailment
      { totalCleared: null, uigf: 10, semidispatchCap: 1 }, // missing value: skipped
    ]);
    expect(summary.cappedIntervals).toBe(3);
    expect(summary.curtailedMwh).toBeCloseTo(1, 10);
    expect(summary.clearedMwh).toBeCloseTo((30 + 50 + 20) / 12, 10);
    expect(summary.intervals).toBe(4);
  });
});

describe("originCategory", () => {
  it.each([
    ["AWEFS_ASEFS", "aemo"],
    ["SCADA", "fallback"],
    ["Last Target", "fallback"],
    ["FCST", "fallback"],
    ["WOOLOOGA", "self"],
  ])("%s -> %s", (origin, category) => {
    expect(originCategory(origin)).toBe(category);
  });
});

describe("assessFreshness", () => {
  const now = nem("2026-10-19T03:00:00");
  it("is ok inside the limit, stale outside, missing with no data", () => {
    expect(assessFreshness(nem("2026-10-19T01:30:00"), 2, now)).toEqual({ status: "ok", ageHours: 1.5 });
    expect(assessFreshness(nem("2026-10-18T22:00:00"), 2, now).status).toBe("stale");
    expect(assessFreshness(null, 2, now)).toEqual({ status: "missing", ageHours: null });
  });
});
