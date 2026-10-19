import { describe, expect, it } from "vitest";
import { cachedTokenProvider, POSTGRES_TOKEN_SCOPE, type AccessToken } from "@/lib/entra-token";
import { azurePoolConfig } from "@/lib/prisma";

function fakeSource(tokens: AccessToken[]) {
  const scopes: string[] = [];
  return {
    scopes,
    async getToken(scope: string) {
      scopes.push(scope);
      return tokens.shift() ?? null;
    },
  };
}

describe("cachedTokenProvider", () => {
  it("reuses a token until five minutes before expiry, then refreshes", async () => {
    let now = 0;
    const source = fakeSource([
      { token: "first", expiresOnTimestamp: 60 * 60 * 1000 },
      { token: "second", expiresOnTimestamp: 2 * 60 * 60 * 1000 },
    ]);
    const password = cachedTokenProvider(source, () => now);

    expect(await password()).toBe("first");
    now = 54 * 60 * 1000;
    expect(await password()).toBe("first");
    now = 56 * 60 * 1000; // inside the 5-minute margin
    expect(await password()).toBe("second");
    expect(source.scopes).toEqual([POSTGRES_TOKEN_SCOPE, POSTGRES_TOKEN_SCOPE]);
  });

  it("makes one token request when several connections open at once", async () => {
    const source = fakeSource([{ token: "only", expiresOnTimestamp: Date.now() + 3_600_000 }]);
    const password = cachedTokenProvider(source);
    expect(await Promise.all([password(), password(), password()])).toEqual(["only", "only", "only"]);
    expect(source.scopes).toHaveLength(1);
  });

  it("fails loudly when the identity returns no token", async () => {
    await expect(cachedTokenProvider(fakeSource([]))()).rejects.toThrow(/no token/);
  });
});

describe("azurePoolConfig", () => {
  it("connects over TLS as the configured identity with a token password", () => {
    const config = azurePoolConfig({ AZURE_POSTGRES_HOST: "db.example", AZURE_POSTGRES_USER: "app-identity" });
    expect(config).toMatchObject({ host: "db.example", user: "app-identity", database: "ops_console", ssl: true });
    expect(typeof config.password).toBe("function");
  });

  it("requires host and user", () => {
    expect(() => azurePoolConfig({ AZURE_POSTGRES_HOST: "db.example" })).toThrow(/AZURE_POSTGRES_USER/);
  });
});
