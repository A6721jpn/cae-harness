"""Trusted, portable provisioning of the reviewed planar compatibility bundle.

The bundle is external evidence, not a caller-authored authority declaration.  Its
identity is pinned by the product and all bytes, typed records, and existing
identities are checked before any source or profile registration is attempted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from febio_cae.adapters.febio import xplt_reader
from febio_cae.domain.artifacts import SourceAssetRef
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import CodecError, decode_record
from febio_cae.domain.compatibility import CompatibilityProfile
from febio_cae.domain.evidence import EvidenceRef
from febio_cae.domain.mesh_policy import NumericalProfileRef
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.storage.mesh_quality import MeshQualityRegistration, decode_mesh_quality
from febio_cae.storage.registry import StorageConflictError

if TYPE_CHECKING:
    from .service import RegisteredCaseService


_APPROVED_BUNDLE_SHA256 = "f5f5ce51367f6f9af6f19fc42da6641fd3b4202711e95681a89333d74a3d22c5"
_APPROVED_BUNDLE_SIZE = 604962
_APPROVED_READER_SHA256 = "8bed227c115babe2e038d0b928194308c4601cf2fd926ac77c4defb2ad9a0ca1"
_SCOPE_CAPABILITY = "febio.scope.planar_linear_frictionless_fixed_xyz"
_PROFILE_IDS = {
    "solver": "febio412-planar-solver-01",
    "outputs": "febio412-planar-outputs-01",
    "quality": "febio412-planar-quality-01",
}
_MESH_PROFILE_ID = "gmsh4152-planar-box-quality-01"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_KEYS = {
    "authority_status",
    "kind",
    "mesh_quality",
    "profiles",
    "publication",
    "schema_version",
    "scope",
    "source_documents",
    "supersedes",
}
_SOURCE_KEYS = {"asset_id", "content_base64", "content_digest", "media_type", "source_kind"}


class _BundleError(ValueError):
    """Internal parse/validation failure converted to an integrity PortError."""


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _BundleError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise _BundleError(f"non-standard JSON constant {value!r}")


def _as_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _BundleError(f"{field} must be a JSON object")
    return cast(dict[str, Any], value)


def _strict_object(value: object, *, required: set[str], field: str) -> dict[str, Any]:
    item = _as_object(value, field)
    if set(item) != required:
        missing = sorted(required - set(item))
        extra = sorted(set(item) - required)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise _BundleError(f"{field} has invalid keys ({'; '.join(detail)})")
    return item


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _BundleError(f"{field} must be non-empty text")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _BundleError(f"{field} contains a control character")
    return value


def _digest(value: object, field: str) -> str:
    result = _text(value, field)
    if _SHA256.fullmatch(result) is None:
        raise _BundleError(f"{field} must be a lowercase SHA-256 digest")
    return result


def _read_bundle(path: Path) -> bytes:
    selected = Path(path).expanduser()
    try:
        if selected.is_symlink():
            raise _BundleError("bundle path must not be a symbolic link")
        attributes = getattr(selected.lstat(), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise _BundleError("bundle path must not be a reparse point")
        info = selected.stat()
    except (OSError, ValueError) as error:
        raise PortError(
            PortErrorCategory.ENVIRONMENT, f"bundle path is unavailable: {error}"
        ) from error
    if not selected.is_file() or info.st_size != _APPROVED_BUNDLE_SIZE:
        raise PortError(
            PortErrorCategory.INTEGRITY,
            f"approved bundle size does not match the pinned {_APPROVED_BUNDLE_SIZE} bytes",
        )
    try:
        with selected.open("rb") as stream:
            content = stream.read(_APPROVED_BUNDLE_SIZE + 1)
    except OSError as error:
        raise PortError(
            PortErrorCategory.ENVIRONMENT, f"cannot read approved bundle: {error}"
        ) from error
    if len(content) != _APPROVED_BUNDLE_SIZE:
        raise PortError(
            PortErrorCategory.INTEGRITY, "approved bundle read was truncated or extended"
        )
    if hashlib.sha256(content).hexdigest() != _APPROVED_BUNDLE_SHA256:
        raise PortError(
            PortErrorCategory.INTEGRITY, "approved bundle digest does not match the product pin"
        )
    return content


def _parse_bundle(
    content: bytes,
) -> tuple[
    dict[str, Any],
    dict[str, bytes],
    dict[str, dict[str, str]],
    dict[str, CompatibilityProfile],
    MeshQualityRegistration,
]:
    try:
        raw = json.loads(
            content,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PortError(
            PortErrorCategory.INTEGRITY, f"approved bundle is not valid JSON: {error}"
        ) from error
    try:
        bundle = _strict_object(raw, required=_BUNDLE_KEYS, field="bundle")
        if bundle["schema_version"] != "1":
            raise _BundleError("bundle schema_version must be '1'")
        if bundle["kind"] != "REVIEWED_PLANAR_COMPATIBILITY_BUNDLE":
            raise _BundleError("bundle kind is not the reviewed planar bundle")
        if bundle["authority_status"] != "PM_APPROVED_FOR_SCOPED_PROVISIONING":
            raise _BundleError("bundle is not approved for scoped provisioning")
        scope = _as_object(bundle["scope"], "bundle.scope")
        if scope.get("enforced_scope_capability") != _SCOPE_CAPABILITY:
            raise _BundleError("bundle scope does not identify the planar capability")
        publication = _as_object(bundle["publication"], "bundle.publication")
        if publication.get("native_operations") != 0:
            raise _BundleError("provisioning bundle contains native operations")
        profiles_raw = _strict_object(
            bundle["profiles"], required=set(_PROFILE_IDS), field="bundle.profiles"
        )
        source_items = bundle["source_documents"]
        if not isinstance(source_items, list) or not source_items:
            raise _BundleError("bundle.source_documents must be a non-empty array")
        sources: dict[str, bytes] = {}
        source_metadata: dict[str, dict[str, str]] = {}
        for index, value in enumerate(source_items):
            source = _strict_object(
                value, required=_SOURCE_KEYS, field=f"source_documents[{index}]"
            )
            asset_id = _text(source["asset_id"], f"source_documents[{index}].asset_id")
            if asset_id in sources:
                raise _BundleError(f"duplicate source asset {asset_id!r}")
            if source["source_kind"] != "registered_document":
                raise _BundleError("approved source documents must be registered documents")
            media_type = _text(source["media_type"], f"source_documents[{index}].media_type")
            content_digest = _digest(
                source["content_digest"], f"source_documents[{index}].content_digest"
            )
            encoded = source["content_base64"]
            if not isinstance(encoded, str):
                raise _BundleError(f"source_documents[{index}].content_base64 must be text")
            try:
                decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (UnicodeError, ValueError, binascii.Error) as error:
                raise _BundleError(f"source_documents[{index}] has invalid base64") from error
            if hashlib.sha256(decoded).hexdigest() != content_digest:
                raise _BundleError(f"source_documents[{index}] bytes do not match content_digest")
            sources[asset_id] = decoded
            source_metadata[asset_id] = {
                "source_kind": "registered_document",
                "media_type": media_type,
                "content_digest": content_digest,
            }

        profiles: dict[str, CompatibilityProfile] = {}
        for purpose, profile_value in profiles_raw.items():
            try:
                profile = decode_record(canonical_bytes(profile_value), CompatibilityProfile)
            except (CodecError, TypeError, ValueError, OverflowError) as error:
                raise _BundleError(f"bundle.profiles.{purpose} is not a typed profile") from error
            if profile.profile_id != _PROFILE_IDS[purpose]:
                raise _BundleError(f"bundle.profiles.{purpose} has an unexpected profile ID")
            if (
                profile.reader.tool_id != "febio-cae-xplt-reader"
                or profile.reader.version != "0.1.0"
                or profile.reader.executable_digest != _APPROVED_READER_SHA256
            ):
                raise _BundleError(f"bundle.profiles.{purpose} has an unapproved reader identity")
            if profile.solver.tool_id != "febio" or profile.solver.version != "4.12.0":
                raise _BundleError(f"bundle.profiles.{purpose} has an unexpected solver identity")
            if not any(
                item.capability_id == _SCOPE_CAPABILITY and item.status.value == "SUPPORTED"
                for item in profile.capabilities
            ):
                raise _BundleError(f"bundle.profiles.{purpose} does not support the planar scope")
            profiles[purpose] = profile

        def check_evidence(evidence: EvidenceRef, field: str) -> None:
            if evidence.reference not in sources:
                raise _BundleError(f"{field} references an unregistered bundle source")
            metadata = source_metadata[evidence.reference]
            if (
                evidence.source_kind != metadata["source_kind"]
                or evidence.content_digest != metadata["content_digest"]
            ):
                raise _BundleError(f"{field} evidence identity differs from source bytes")

        for purpose, profile in profiles.items():
            for evidence in profile.evidence:
                check_evidence(evidence, f"bundle.profiles.{purpose}.evidence")
            for index, capability in enumerate(profile.capabilities):
                for evidence in capability.evidence:
                    check_evidence(
                        evidence, f"bundle.profiles.{purpose}.capabilities[{index}].evidence"
                    )

        try:
            mesh = decode_mesh_quality(canonical_bytes(bundle["mesh_quality"]))
        except (TypeError, ValueError, KeyError, OverflowError, json.JSONDecodeError) as error:
            raise _BundleError("bundle.mesh_quality is not a typed mesh-quality record") from error
        if not isinstance(mesh, MeshQualityRegistration) or mesh.profile_id != _MESH_PROFILE_ID:
            raise _BundleError("bundle mesh quality is not the approved generation record")
        for evidence in mesh.qualification_evidence:
            check_evidence(evidence, "bundle.mesh_quality.qualification_evidence")
    except _BundleError as error:
        raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error
    return bundle, sources, source_metadata, profiles, mesh


def _prevalidate_existing(
    service: RegisteredCaseService,
    case_id: str,
    profiles: Mapping[str, CompatibilityProfile],
    mesh: MeshQualityRegistration,
    source_metadata: Mapping[str, Mapping[str, str]],
    source_bytes: Mapping[str, bytes],
) -> None:
    if not callable(getattr(service.compatibility, "register", None)):
        raise PortError(
            PortErrorCategory.CONFLICT,
            "configured compatibility registry is read-only",
        )
    storage = service._storage(case_id)
    for asset_id, metadata in source_metadata.items():
        expected = SourceAssetRef(asset_id, metadata["content_digest"], metadata["media_type"])
        try:
            current = storage.source_asset(asset_id)
        except StorageConflictError:
            continue
        if current != expected or storage.source_kind(asset_id) != metadata["source_kind"]:
            raise PortError(
                PortErrorCategory.CONFLICT, f"source identity {asset_id!r} is already different"
            )
        try:
            resolved = storage.resolve_source(current)
        except PortError as error:
            raise PortError(PortErrorCategory.INTEGRITY, str(error)) from error
        if resolved.content != source_bytes[asset_id]:
            raise PortError(PortErrorCategory.INTEGRITY, f"source bytes for {asset_id!r} differ")

    for profile in profiles.values():
        try:
            current = service.compatibility.get_profile(profile.profile_id)
        except PortError as error:
            if error.category is PortErrorCategory.UNSUPPORTED_CAPABILITY:
                continue
            raise
        if current.to_bytes() != profile.to_bytes():
            raise PortError(
                PortErrorCategory.CONFLICT,
                f"compatibility profile {profile.profile_id!r} is already registered differently",
            )

    try:
        service.resolve_mesh_quality(case_id, mesh.reference)
    except PortError as error:
        if error.category is not PortErrorCategory.UNSUPPORTED_CAPABILITY:
            raise


def provision_planar_profiles(
    service: RegisteredCaseService,
    case_id: str,
    *,
    bundle_path: Path,
) -> dict[str, object]:
    """Validate and publish the exact approved planar records without native work."""
    content = _read_bundle(bundle_path)
    bundle, source_bytes, source_metadata, profiles, mesh = _parse_bundle(content)
    try:
        reader_payload = Path(xplt_reader.__file__).read_bytes()
    except OSError as error:
        raise PortError(
            PortErrorCategory.INTEGRITY, "installed reader bytes cannot be verified"
        ) from error
    if hashlib.sha256(reader_payload).hexdigest() != _APPROVED_READER_SHA256:
        raise PortError(
            PortErrorCategory.INTEGRITY, "installed reader differs from the qualified reader"
        )

    # Preflight current identities; stores also enforce conflicts during publication.
    _prevalidate_existing(service, case_id, profiles, mesh, source_metadata, source_bytes)
    storage = service._storage(case_id)
    for asset_id, metadata in source_metadata.items():
        storage.ingest_source(
            asset_id=asset_id,
            source_kind=metadata["source_kind"],
            media_type=metadata["media_type"],
            content=source_bytes[asset_id],
            expected_digest=metadata["content_digest"],
        )
    service.register_mesh_quality(case_id, mesh)
    for profile in profiles.values():
        service.register_profile(profile)

    result_profiles: dict[str, object] = {}
    for purpose, profile in profiles.items():
        result_profiles[purpose] = NumericalProfileRef(
            profile.profile_id,
            purpose,
            hashlib.sha256(profile.to_bytes()).hexdigest(),
        ).to_dict()
    return {
        "schema_version": "1",
        "status": "PROVISIONED",
        "case_id": case_id,
        "profiles": result_profiles,
        "mesh_quality": mesh.reference.to_dict(),
        "approved_bundle": {"sha256": _APPROVED_BUNDLE_SHA256, "size": _APPROVED_BUNDLE_SIZE},
        "scope": cast(dict[str, object], bundle["scope"]),
        "native_operations": 0,
    }


__all__ = [
    "provision_planar_profiles",
]
