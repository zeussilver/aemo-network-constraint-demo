-- Run against the `ops_console` database after migrations. The app gets data access
-- only: no DDL, so schema changes stay with the deploy identity that runs migrations.
--   psql -v app_role=<web app name> -f app-role-grants.sql
GRANT CONNECT ON DATABASE ops_console TO :"app_role";
GRANT USAGE ON SCHEMA public TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";
