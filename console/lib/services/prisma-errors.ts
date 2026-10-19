/** Prisma error codes the services translate into user-facing errors. */
export function prismaCode(error: unknown): string | undefined {
  if (typeof error === "object" && error !== null && "code" in error) {
    const code = (error as { code?: unknown }).code;
    return typeof code === "string" ? code : undefined;
  }
  return undefined;
}

export const UNIQUE_VIOLATION = "P2002";
export const FOREIGN_KEY_VIOLATION = "P2003";
