/**
 * Minimal RFC 4180 CSV parser for small committed reference files (data/site_registry.csv).
 * Handles quoted fields, escaped quotes ("") and CRLF line endings.
 */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (inQuotes) {
      if (char === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (inQuotes) {
    throw new Error("Unterminated quoted field in CSV input.");
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  const [header, ...body] = rows.filter((r) => !(r.length === 1 && r[0] === ""));
  if (!header) return [];
  return body.map((values, index) => {
    if (values.length !== header.length) {
      throw new Error(`CSV row ${index + 2} has ${values.length} fields, expected ${header.length}.`);
    }
    return Object.fromEntries(header.map((name, j) => [name, values[j]]));
  });
}
