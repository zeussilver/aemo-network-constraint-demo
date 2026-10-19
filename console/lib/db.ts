import "server-only";
import { createPrismaClient } from "./prisma";
import type { PrismaClient } from "./generated/prisma/client";

// One client per server process. In dev, hot reload re-evaluates modules, so the client
// is parked on globalThis to avoid opening a new connection pool on every edit.
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const db = globalForPrisma.prisma ?? createPrismaClient();

if (process.env.NODE_ENV !== "production") {
  globalForPrisma.prisma = db;
}
