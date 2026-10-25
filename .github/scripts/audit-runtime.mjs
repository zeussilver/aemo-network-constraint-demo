// Gate for `npm audit --omit=dev --json`: fail on any high or critical advisory in the
// console's runtime dependencies, except the advisories listed in ALLOWED.
//
// Every entry says why it does not apply here and when to remove it. An entry that npm
// no longer reports is flagged so the list does not go stale.
//
// Usage: node .github/scripts/audit-runtime.mjs <npm-audit.json>
import { readFileSync } from "node:fs";

const ALLOWED = {
  "GHSA-xcx6-jx8p-x3h5":
    "@azure/msal-node < 7.1.0, authorization code flow. The console only uses " +
    "ManagedIdentityCredential and AzureCliCredential (console/lib/prisma.ts), never the " +
    "authorization code flow, and the latest @azure/identity (4.14.0) still requires " +
    "@azure/msal-node ^6.0.0. Remove once @azure/identity accepts @azure/msal-node >= 7.1.0.",
};

const BLOCKING = new Set(["high", "critical"]);

const report = JSON.parse(readFileSync(process.argv[2], "utf8"));
if (report.error || typeof report.vulnerabilities !== "object") {
  console.log(`::error::npm audit did not produce a report: ${JSON.stringify(report.error ?? report)}`);
  process.exit(1);
}

// Each vulnerable chain ends in an advisory object; entries for dependents only name the
// package they inherit from, so the advisories alone cover everything.
const advisories = new Map();
for (const vuln of Object.values(report.vulnerabilities)) {
  for (const via of vuln.via) {
    if (typeof via === "object") advisories.set(via.url, via);
  }
}

let blocked = 0;
const seen = new Set();
for (const advisory of advisories.values()) {
  const id = advisory.url.split("/").pop();
  const label = `${advisory.severity} ${id} in ${advisory.name}: ${advisory.title}`;
  if (id in ALLOWED) {
    seen.add(id);
    console.log(`::warning::Allowed ${label}. ${ALLOWED[id]}`);
  } else if (BLOCKING.has(advisory.severity)) {
    blocked += 1;
    console.log(`::error::${label} (${advisory.url})`);
  } else {
    console.log(`Below the high threshold: ${label}`);
  }
}

for (const id of Object.keys(ALLOWED)) {
  if (!seen.has(id)) {
    console.log(`::notice::${id} is no longer reported; remove it from ALLOWED.`);
  }
}

if (blocked > 0) {
  console.log(`${blocked} high or critical advisor${blocked === 1 ? "y" : "ies"} in runtime dependencies.`);
  process.exit(1);
}
console.log("No unallowed high or critical advisories in runtime dependencies.");
