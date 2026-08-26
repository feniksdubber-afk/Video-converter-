"""initial dubbing job/artifact core schema

Revision ID: 0001
Revises:
Create Date: 2026-08-26

Faqat afsona_dubbing (yoki afsona_dubbing_test) bazasida ishga tushirilishi
mo'ljallangan. env.py bu migratsiyani boshqa DSN'ga qarshi ishga
tushirishni rad etadi (xavfsizlik to'sig'i).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "episodes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_name", sa.Text(), nullable=False),
        sa.Column("original_r2_key", sa.Text(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("metadata", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.BigInteger(), sa.ForeignKey("episodes.id"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("depends_on_job_id", sa.BigInteger(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("leased_by", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_kind", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("episode_id", "stage", "input_hash", name="jobs_dedup"),
    )
    op.create_index("idx_jobs_status_stage", "jobs", ["status", "stage"])
    op.create_index("idx_jobs_episode_status", "jobs", ["episode_id", "status"])
    op.create_index(
        "idx_jobs_lease_expiry", "jobs", ["lease_expires_at"],
        postgresql_where=sa.text("status = 'processing'"),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.BigInteger(), sa.ForeignKey("episodes.id"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("engine_name", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("params", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("producing_job_id", sa.BigInteger(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("episode_id", "stage", "content_hash", name="artifacts_cache_key"),
    )
    op.create_index("idx_artifacts_episode_stage", "artifacts", ["episode_id", "stage"])

    op.create_table(
        "artifact_lineage",
        sa.Column("artifact_id", sa.BigInteger(), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("parent_artifact_id", sa.BigInteger(), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id", "parent_artifact_id"),
    )


def downgrade() -> None:
    op.drop_table("artifact_lineage")
    op.drop_index("idx_artifacts_episode_stage", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("idx_jobs_lease_expiry", table_name="jobs")
    op.drop_index("idx_jobs_episode_status", table_name="jobs")
    op.drop_index("idx_jobs_status_stage", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("episodes")
