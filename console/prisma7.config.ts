// Prisma 7.10 reads prisma7.config.ts first (prisma.config.ts is the fallback). Prisma 7 no
// longer loads .env on its own, hence the dotenv import.
import "dotenv/config";
import { defineConfig } from "prisma/config";

export default defineConfig({
  schema: "prisma/schema.prisma",
  migrations: {
    path: "prisma/migrations",
    seed: "tsx prisma/seed.ts",
  },
  datasource: {
    url: process.env["DATABASE_URL"],
  },
});
