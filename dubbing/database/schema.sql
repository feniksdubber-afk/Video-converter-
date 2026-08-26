-- dubbing/database/schema.sql
--
-- Kanonik, o'qish uchun mo'ljallangan sxema hujjati. Bu fayl to'g'ridan-to'g'ri
-- ishga tushirilmaydi — haqiqiy migratsiya mexanizmi Alembic
-- (dubbing/database/migrations/) orqali amalga oshiriladi. Bu fayl faqat
-- joriy sxema holatini bir qarashda ko'rish uchun saqlanadi.
--
-- MUHIM: bu sxema FAQAT alohida "afsona_dubbing" PostgreSQL bazasida
-- yaratiladi. Mavjud botning SQLite bazasiga yoki AfsonaMovieBot'ning
-- kinobot.db fayliga hech qanday aloqasi yo'q.

CREATE TABLE episodes (
    id              BIGSERIAL PRIMARY KEY,
    project_name    TEXT NOT NULL,
    original_r2_key TEXT NOT NULL,
    duration_sec    DOUBLE PRECISION,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_by      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id                 BIGSERIAL PRIMARY KEY,
    episode_id         BIGINT NOT NULL REFERENCES episodes(id),
    stage              TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'queued',
    depends_on_job_id  BIGINT REFERENCES jobs(id),
    input_hash         TEXT,
    priority           INT NOT NULL DEFAULT 100,
    attempts           INT NOT NULL DEFAULT 0,
    max_attempts       INT NOT NULL DEFAULT 3,
    leased_by          TEXT,
    lease_expires_at   TIMESTAMPTZ,
    error              TEXT,
    error_kind         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,

    CONSTRAINT jobs_dedup UNIQUE (episode_id, stage, input_hash)
);
CREATE INDEX idx_jobs_status_stage ON jobs (status, stage);
CREATE INDEX idx_jobs_episode_status ON jobs (episode_id, status);
CREATE INDEX idx_jobs_lease_expiry ON jobs (lease_expires_at) WHERE status = 'processing';

CREATE TABLE artifacts (
    id               BIGSERIAL PRIMARY KEY,
    episode_id       BIGINT NOT NULL REFERENCES episodes(id),
    stage            TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    storage_key      TEXT,
    engine_name      TEXT NOT NULL,
    engine_version   TEXT NOT NULL,
    params           JSONB NOT NULL DEFAULT '{}',
    producing_job_id BIGINT REFERENCES jobs(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT artifacts_cache_key UNIQUE (episode_id, stage, content_hash)
);
CREATE INDEX idx_artifacts_episode_stage ON artifacts (episode_id, stage);

CREATE TABLE artifact_lineage (
    artifact_id        BIGINT NOT NULL REFERENCES artifacts(id),
    parent_artifact_id BIGINT NOT NULL REFERENCES artifacts(id),
    PRIMARY KEY (artifact_id, parent_artifact_id)
);
