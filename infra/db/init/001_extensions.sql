-- Runs once, the first time the LOCAL Docker database volume is created.
-- Cloud SQL never runs this file: from M1 on, migrations own the schema and also run
-- this (idempotent) statement, so local and cloud end up identical.
CREATE EXTENSION IF NOT EXISTS vector;
