try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETE = "complete"


class SourceStatus(StrEnum):
    PENDING = "pending"
    RECEIVING = "receiving"
    STORED = "stored"
    SKIPPED = "skipped"
    DUPLICATE = "duplicate"
    PARSING = "parsing"
    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"
    DEGRADED_MISSING_ARTIFACT = "degraded_missing_artifact"
