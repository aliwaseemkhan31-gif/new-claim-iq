-- ClaimIQ Enterprise: one-time local database setup (without Docker).
--
-- Run as the PostgreSQL superuser, choosing a password for the application's
-- own role:
--
--   psql -U postgres -v app_password="choose-a-strong-password" -f scripts/setup_local_db.sql
--
-- then set the same value as POSTGRES_PASSWORD in .env. The password is passed
-- in rather than written here, so this file is safe to commit — but it will be
-- recorded in your shell history, so clear that on a shared machine.
--
-- Superuser is needed for one reason only: pgvector is not a trusted
-- extension, so the application role cannot run CREATE EXTENSION itself. The
-- role created below is deliberately not a superuser.
--
-- Safe to re-run: existing objects are kept, and the role's password is reset.

\set ON_ERROR_STOP on

\if :{?app_password}
\else
  \echo 'Missing password. Re-run with: -v app_password="..."'
  DO $$ BEGIN RAISE EXCEPTION 'app_password is required'; END $$;
\endif

-- ---------------------------------------------------------------------------
-- 1. Application role
--
-- NOSUPERUSER / NOCREATEROLE: the application never needs either, and a
-- compromised application credential should not be able to escalate.
-- CREATEDB: Django's test runner creates and drops its own database.
-- format(%L) quotes the password safely; psql variables cannot be interpolated
-- inside a DO block, hence \gexec.
-- ---------------------------------------------------------------------------
SELECT format(
    'CREATE ROLE claimiq LOGIN NOSUPERUSER NOCREATEROLE CREATEDB PASSWORD %L',
    :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'claimiq')
\gexec

SELECT format(
    'ALTER ROLE claimiq LOGIN NOSUPERUSER NOCREATEROLE CREATEDB PASSWORD %L',
    :'app_password')
\gexec

-- ---------------------------------------------------------------------------
-- 2. Database (CREATE DATABASE cannot run inside a transaction or DO block)
-- ---------------------------------------------------------------------------
SELECT 'CREATE DATABASE claimiq OWNER claimiq ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'claimiq')
\gexec

-- ---------------------------------------------------------------------------
-- 3. Extensions and text search configuration, in the application database
--    and in template1.
--
-- template1 matters: Django builds its test database from it, and without the
-- extensions there every test touching a vector column fails with
-- "type vector does not exist".
-- ---------------------------------------------------------------------------
\connect claimiq

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'claimiq_english') THEN
        CREATE TEXT SEARCH CONFIGURATION claimiq_english (COPY = english);
        ALTER TEXT SEARCH CONFIGURATION claimiq_english
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, english_stem;
    END IF;
END
$$;

\connect template1

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'claimiq_english') THEN
        CREATE TEXT SEARCH CONFIGURATION claimiq_english (COPY = english);
        ALTER TEXT SEARCH CONFIGURATION claimiq_english
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, english_stem;
    END IF;
END
$$;

\connect claimiq
SELECT extname, extversion FROM pg_extension ORDER BY extname;
SELECT 'ClaimIQ database setup complete' AS status;
