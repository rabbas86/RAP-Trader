"""Phase 8A Unified Research Data Platform service layer."""

from app.domain.models.data_platform import SnapshotRequest

from .calendar import CalendarService, ResearchCalendarService
from .fingerprint import DataFingerprintService, FingerprintService, canonical_json, sha256_fingerprint
from .freshness import DataFreshnessService, FreshnessService
from .normalization import DataNormalizationService, NormalizationService
from .provenance import DataProvenanceService, ProvenanceService
from .quality import DataQualityService, QualityService
from .registry import DataSourceRegistry
from .revisions import PointInTimeRevisionService, RevisionService
from .service import DataPlatformService, UnifiedResearchDataPlatformService
from .snapshot import ResearchDataSnapshotService, SnapshotService
from .store import DataRecordStore, InMemoryDataRecordStore, JSONFileDataRecordStore
from .validation import DataValidationService, ValidationService

__all__ = [
    "CalendarService",
    "DataFingerprintService",
    "DataFreshnessService",
    "DataNormalizationService",
    "DataPlatformService",
    "DataProvenanceService",
    "DataQualityService",
    "DataRecordStore",
    "DataSourceRegistry",
    "DataValidationService",
    "FingerprintService",
    "FreshnessService",
    "InMemoryDataRecordStore",
    "JSONFileDataRecordStore",
    "NormalizationService",
    "PointInTimeRevisionService",
    "ProvenanceService",
    "QualityService",
    "ResearchCalendarService",
    "ResearchDataSnapshotService",
    "RevisionService",
    "SnapshotRequest",
    "SnapshotService",
    "UnifiedResearchDataPlatformService",
    "ValidationService",
    "canonical_json",
    "sha256_fingerprint",
]
