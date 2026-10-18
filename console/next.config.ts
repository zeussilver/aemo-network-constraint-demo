import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle for Azure App Service (node .next/standalone/server.js).
  output: "standalone",
  // Cache Components (the create-next-app default) is left off: every console page is an
  // authenticated, per-request view, so there is no static shell to prerender.
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
