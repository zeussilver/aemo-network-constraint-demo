import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle for Azure App Service (node .next/standalone/server.js).
  output: "standalone",
  // Keep `next dev` from writing an AGENTS.md file into the project.
  agentRules: false,
  // Cache Components (the create-next-app default) is left off: every console page is an
  // authenticated, per-request view, so there is no static shell to prerender.
  // OpenTelemetry instrumentation patches modules at runtime, so it must stay a real
  // node_modules package rather than be bundled.
  serverExternalPackages: ["@azure/monitor-opentelemetry", "@azure/identity"],
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
