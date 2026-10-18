type Raw = Record<string, string | string[] | undefined>;

export function param(searchParams: Raw, key: string): string | undefined {
  const value = searchParams[key];
  const first = Array.isArray(value) ? value[0] : value;
  const trimmed = first?.trim();
  return trimmed ? trimmed : undefined;
}

export function pageParam(searchParams: Raw): number {
  const page = Number(param(searchParams, "page") ?? 1);
  return Number.isInteger(page) && page > 0 ? page : 1;
}

/** Accept a query value only if it is one of the allowed literals. */
export function enumParam<T extends string>(searchParams: Raw, key: string, allowed: readonly T[]): T | undefined {
  const value = param(searchParams, key);
  return value && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;
}

export function flagParam(searchParams: Raw, key: string): boolean {
  return param(searchParams, key) === "1";
}

/** Build a query string, dropping empty values, for filter and pagination links. */
export function withParams(base: string, params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === false) continue;
    query.set(key, value === true ? "1" : String(value));
  }
  const text = query.toString();
  return text ? `${base}?${text}` : base;
}
