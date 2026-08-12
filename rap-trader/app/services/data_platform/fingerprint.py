"""Backward-compatible access to canonical serialization and fingerprints."""

from app.domain.canonical import canonical_bytes, canonical_json, sha256_fingerprint


class DataFingerprintService:
    canonical_json = staticmethod(canonical_json)
    fingerprint = staticmethod(sha256_fingerprint)


FingerprintService = DataFingerprintService

__all__ = [
    "DataFingerprintService",
    "FingerprintService",
    "canonical_bytes",
    "canonical_json",
    "sha256_fingerprint",
]
