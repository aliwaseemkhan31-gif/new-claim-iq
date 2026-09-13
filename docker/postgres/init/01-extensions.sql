-- ClaimIQ Enterprise — database initialisation.
-- Runs once, on first cluster creation only.

-- Dense vector storage and ANN indexing (ADR 0002).
CREATE EXTENSION IF NOT EXISTS vector;

-- Trigram matching. Used for fuzzy lookup of party names, document references
-- and defined terms, where an exact match is too strict and an embedding is
-- the wrong tool.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Accent-insensitive matching for party and place names.
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Query statistics, for diagnosing slow retrieval in production.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- A text search configuration that folds accents before stemming, so
-- "Böhler" and "Bohler" match. Django references this by name.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_ts_config WHERE cfgname = 'claimiq_english'
    ) THEN
        CREATE TEXT SEARCH CONFIGURATION claimiq_english (COPY = english);
        ALTER TEXT SEARCH CONFIGURATION claimiq_english
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, english_stem;
    END IF;
END
$$;
