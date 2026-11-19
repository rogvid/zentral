-- Keycloak's login and database on the shared PostgreSQL (run by the idp-db
-- service in compose.idp.yaml). Safe to run on every start.
SELECT format('CREATE ROLE keycloak LOGIN PASSWORD %L', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak')
\gexec
SELECT 'CREATE DATABASE keycloak OWNER keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')
\gexec
