-- Run against the `postgres` database as an Entra administrator (the deploy workflow).
-- Creates a login role bound to the web app's system-assigned managed identity, so the
-- app signs in with an Entra token and no password exists.
--   psql -v app_role=<web app name> -v app_oid=<identity principal id> -f app-role-create.sql
SELECT pgaadauth_create_principal_with_oid(:'app_role', :'app_oid', 'service', false, false)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role');
