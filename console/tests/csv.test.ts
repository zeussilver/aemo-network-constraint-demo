import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parseCsv } from "@/lib/csv";

describe("parseCsv", () => {
  it("parses quoted fields, escaped quotes and CRLF", () => {
    const rows = parseCsv('name,note\r\n"Smith, J","said ""hi"""\r\nplain,\r\n');
    expect(rows).toEqual([
      { name: "Smith, J", note: 'said "hi"' },
      { name: "plain", note: "" },
    ]);
  });

  it("rejects rows with the wrong field count", () => {
    expect(() => parseCsv("a,b\n1,2,3\n")).toThrow(/expected 2/);
  });

  it("rejects an unterminated quote", () => {
    expect(() => parseCsv('a\n"open\n')).toThrow(/Unterminated/);
  });

  it("reads the committed site registry", () => {
    const text = readFileSync(path.join(__dirname, "..", "..", "data", "site_registry.csv"), "utf8");
    const rows = parseCsv(text);
    expect(rows.length).toBeGreaterThanOrEqual(10);
    for (const row of rows) {
      expect(row.duid).toMatch(/^[A-Z0-9]+$/);
      expect(["SOLAR", "WIND"]).toContain(row.fuel);
      expect(Number(row.registered_capacity_mw)).toBeGreaterThan(0);
    }
  });
});
