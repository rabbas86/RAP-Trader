"""Schema and platform version constants for the Market Intelligence Feature Platform.

These constants are embedded in cache keys and provenance records so that
incompatible versions never share cached results.
"""

from __future__ import annotations

#: Canonical MIFP feature schema version.
#: Bumped when the FeatureValue/FeatureSnapshot model shape changes incompatibly.
FEATURE_SCHEMA_VERSION: str = "1.0.0"

#: Platform version string embedded in cache keys and provenance.
PLATFORM_VERSION: str = "mifp-6.5.0"

#: Generator version for the bundled deterministic feature generators.
GENERATOR_VERSION: str = "1.0.0"
