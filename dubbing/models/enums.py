"""
dubbing/models/enums.py — Job va artifact holatlari uchun enum'lar.

Bu modul mavjud botning hech qanday enum/konstantasiga bog'liq emas.
"""

from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal_states(cls) -> frozenset["JobStatus"]:
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELLED})


class JobErrorKind(str, Enum):
    TIMEOUT = "timeout"
    OOM = "oom"
    EXCEPTION = "exception"


class JobStage(str, Enum):
    """
    MVP pipeline bosqichlari (Step 1'da faqat state-machine/placeholder
    darajasida ishlatiladi — haqiqiy engine kodlari keyingi bosqichlarda
    qo'shiladi).
    """
    INGESTION = "ingestion"
    SEGMENTATION = "segmentation"
    TRANSCRIPTION = "transcription"
    DIARIZATION = "diarization"
    ANALYSIS = "analysis"
    MIXING = "mixing"
    QC = "qc"
    # Step 1 test/placeholder uchun maxsus bosqich — hech qanday real
    # media-processing kodiga bog'liq emas.
    PLACEHOLDER = "placeholder"
