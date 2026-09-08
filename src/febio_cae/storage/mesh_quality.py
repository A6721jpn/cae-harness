"""Private immutable mesh-quality configuration, not a public domain codec."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.evidence import EvidenceRef
from febio_cae.domain.mesh_policy import NumericalProfileRef
from febio_cae.domain.units import Dimension, Quantity


@dataclass(frozen=True, slots=True)
class MeshQualityRegistration:
    profile_id: str
    max_boundary_deviation: Quantity
    primitive_kinds: tuple[str, ...]
    algorithm_id: str
    algorithm_version: str
    evidence_scope: str
    qualification_evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        for value in (self.profile_id, self.algorithm_id, self.algorithm_version):
            if not isinstance(value, str) or not value or value.strip() != value:
                raise ValueError("mesh quality identity must be explicit nonempty text")
        limit = self.max_boundary_deviation
        if (
            not isinstance(limit, Quantity)
            or limit.dimension != Dimension(length=1)
            or not math.isfinite(limit.to_si().value)
            or limit.to_si().value <= 0
        ):
            raise ValueError("mesh quality requires an explicit finite positive length")
        kinds = tuple(sorted(set(self.primitive_kinds)))
        if not kinds or any(kind not in {"sphere", "cylinder", "box"} for kind in kinds):
            raise ValueError("mesh quality primitive kinds are unsupported")
        if self.evidence_scope != "synthetic":
            raise ValueError("only explicit synthetic qualification is implemented")
        evidence = tuple(self.qualification_evidence)
        if not evidence or any(
            not isinstance(item, EvidenceRef) or item.target_field != "mesh_quality.qualification"
            for item in evidence
        ):
            raise ValueError("explicit mesh quality qualification evidence is required")
        unique = {canonical_bytes(item.to_dict()): item for item in evidence}
        object.__setattr__(self, "primitive_kinds", kinds)
        object.__setattr__(self, "qualification_evidence", tuple(unique[k] for k in sorted(unique)))
        object.__setattr__(self, "max_boundary_deviation", limit.to_si())

    def to_bytes(self) -> bytes:
        return canonical_bytes(
            {
                "format_version": 1,
                "profile_id": self.profile_id,
                "purpose": "mesh_quality",
                "max_boundary_deviation": {
                    "value": self.max_boundary_deviation.value,
                    "unit": self.max_boundary_deviation.unit,
                },
                "primitive_kinds": list(self.primitive_kinds),
                "algorithm_id": self.algorithm_id,
                "algorithm_version": self.algorithm_version,
                "evidence_scope": self.evidence_scope,
                "qualification_evidence": [item.to_dict() for item in self.qualification_evidence],
            }
        )

    @property
    def reference(self) -> NumericalProfileRef:
        return NumericalProfileRef(
            self.profile_id, "mesh_quality", hashlib.sha256(self.to_bytes()).hexdigest()
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> MeshQualityRegistration:
        data = json.loads(payload)
        if (
            not isinstance(data, dict)
            or set(data)
            != {
                "format_version",
                "profile_id",
                "purpose",
                "max_boundary_deviation",
                "primitive_kinds",
                "algorithm_id",
                "algorithm_version",
                "evidence_scope",
                "qualification_evidence",
            }
            or data["format_version"] != 1
            or data["purpose"] != "mesh_quality"
        ):
            raise ValueError("invalid private mesh quality payload")
        result = cls(
            data["profile_id"],
            Quantity(**data["max_boundary_deviation"]),
            tuple(data["primitive_kinds"]),
            data["algorithm_id"],
            data["algorithm_version"],
            data["evidence_scope"],
            tuple(EvidenceRef(**item) for item in data["qualification_evidence"]),
        )
        if result.to_bytes() != payload:
            raise ValueError("mesh quality payload is not canonical")
        return result
