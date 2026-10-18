/** NEM market time is fixed UTC+10 (AEST, no daylight saving): Brisbane's zone. */
export const NEM_TIME_ZONE = "Australia/Brisbane";
/** Staff-facing timestamps (audit log, edits) use Melbourne local time. */
export const STAFF_TIME_ZONE = "Australia/Melbourne";

const staffDateTime = new Intl.DateTimeFormat("en-AU", {
  timeZone: STAFF_TIME_ZONE,
  dateStyle: "medium",
  timeStyle: "short",
});

const nemDateTime = new Intl.DateTimeFormat("en-AU", {
  timeZone: NEM_TIME_ZONE,
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export function formatStaffTime(value: Date | string | null | undefined): string {
  return value ? staffDateTime.format(new Date(value)) : "—";
}

export function formatNemTime(value: Date | string | null | undefined): string {
  return value ? `${nemDateTime.format(new Date(value))} NEM` : "—";
}

export function formatMw(value: number | null | undefined, digits = 1): string {
  return value == null || Number.isNaN(value) ? "—" : `${value.toFixed(digits)} MW`;
}

export function titleCase(value: string): string {
  return value.charAt(0) + value.slice(1).toLowerCase();
}
