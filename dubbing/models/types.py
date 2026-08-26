"""
dubbing/models/types.py — Job/Artifact yozuvlari uchun dataclass'lar.

Bu modul faqat dubbing.models.enums'ga bog'liq — boshqa hech narsaga.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from dubbing.models.enums import JobStatus


@dataclass(frozen=True)
class JobRecord:
    id: int
    episode_id: int
    stage: str
    status: JobStatus
    depends_on_job_id: Optional[int]
    input_hash: Optional[str]
    priority: int
    attempts: int
    max_attempts: int
    leased_by: Optional[str]
    lease_expires_at: Optional[datetime]
    error: Optional[str]
    error_kind: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    @classmethod
    def from_row(cls, row: Any) -> "JobRecord":
        return cls(
            id=row["id"],
            episode_id=row["episode_id"],
            stage=row["stage"],
            status=JobStatus(row["status"]),
            depends_on_job_id=row["depends_on_job_id"],
            input_hash=row["input_hash"],
            priority=row["priority"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            leased_by=row["leased_by"],
            lease_expires_at=row["lease_expires_at"],
            error=row["error"],
            error_kind=row["error_kind"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


@dataclass(frozen=True)
class ArtifactRecord:
    id: int
    episode_id: int
    stage: str
    content_hash: str
    storage_key: Optional[str]
    engine_name: str
    engine_version: str
    params: dict
    producing_job_id: Optional[int]
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> "ArtifactRecord":
        import json
        params = row["params"]
        if isinstance(params, str):
            params = json.loads(params)
        return cls(
            id=row["id"],
            episode_id=row["episode_id"],
            stage=row["stage"],
            content_hash=row["content_hash"],
            storage_key=row["storage_key"],
            engine_name=row["engine_name"],
            engine_version=row["engine_version"],
            params=params,
            producing_job_id=row["producing_job_id"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class LeaseInfo:
    job_id: int
    worker_id: str
    lease_expires_at: datetime
