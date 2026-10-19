import { AzureCliCredential, ManagedIdentityCredential, type TokenCredential } from "@azure/identity";
import { PrismaPg } from "@prisma/adapter-pg";
import type { PoolConfig } from "pg";
import { cachedTokenProvider } from "./entra-token";
import { PrismaClient } from "./generated/prisma/client";

/**
 * Connection settings for Azure Database for PostgreSQL with Microsoft Entra auth only.
 * The server has password login disabled; the token from the managed identity (or, for
 * an admin running locally, from `az login`) is the password.
 */
export function azurePoolConfig(env: Record<string, string | undefined> = process.env): PoolConfig {
  const host = env.AZURE_POSTGRES_HOST;
  const user = env.AZURE_POSTGRES_USER;
  if (!host || !user) throw new Error("AZURE_POSTGRES_HOST and AZURE_POSTGRES_USER must both be set.");
  const credential: TokenCredential =
    env.AZURE_POSTGRES_USE_CLI === "1"
      ? new AzureCliCredential()
      : new ManagedIdentityCredential(env.AZURE_CLIENT_ID ? { clientId: env.AZURE_CLIENT_ID } : {});
  return {
    host,
    port: 5432,
    user,
    database: env.AZURE_POSTGRES_DATABASE ?? "ops_console",
    ssl: true,
    max: 5,
    password: cachedTokenProvider(credential),
  };
}

/**
 * Build a Prisma client on the node-postgres driver adapter (required since Prisma 7).
 * An explicit connection string (DATABASE_URL locally and in CI) wins; on Azure there is
 * none, and AZURE_POSTGRES_HOST switches to Entra token auth.
 */
export function createPrismaClient(connectionString = process.env.DATABASE_URL): PrismaClient {
  if (connectionString) {
    return new PrismaClient({ adapter: new PrismaPg({ connectionString }) });
  }
  if (process.env.AZURE_POSTGRES_HOST) {
    return new PrismaClient({ adapter: new PrismaPg(azurePoolConfig()) });
  }
  throw new Error("Set DATABASE_URL, or AZURE_POSTGRES_HOST and AZURE_POSTGRES_USER for Entra auth on Azure.");
}
