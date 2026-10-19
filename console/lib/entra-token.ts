/**
 * Microsoft Entra access tokens for Azure Database for PostgreSQL. On Azure the console
 * has no database password: it signs in as its managed identity, and the token is the
 * password. node-postgres calls the returned function for every new connection, so the
 * token is cached and refreshed five minutes before it expires.
 */
export const POSTGRES_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default";
const REFRESH_MARGIN_MS = 5 * 60 * 1000;

export type AccessToken = { token: string; expiresOnTimestamp: number };
export type TokenSource = { getToken(scope: string): Promise<AccessToken | null> };

export function cachedTokenProvider(source: TokenSource, now: () => number = Date.now): () => Promise<string> {
  let cached: AccessToken | null = null;
  let pending: Promise<AccessToken> | null = null;

  return async () => {
    if (cached && cached.expiresOnTimestamp - REFRESH_MARGIN_MS > now()) return cached.token;
    // One refresh at a time, even when several connections open at once.
    pending ??= source.getToken(POSTGRES_TOKEN_SCOPE).then((token) => {
      if (!token) throw new Error("Managed identity returned no token for Azure Database for PostgreSQL.");
      return token;
    });
    try {
      cached = await pending;
      return cached.token;
    } finally {
      pending = null;
    }
  };
}
