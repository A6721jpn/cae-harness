"""Optional Gmsh/OpenCASCADE implementation of the geometry backend boundary.

The product deliberately does not depend on Gmsh at import time.  A caller
provides an explicit configuration and the backend imports the configured
Python module only when an operation is requested.  Every native call is made
inside an exclusively owned, short-lived Gmsh session. An already initialized
session is refused without modifying caller state. OCC import explicitly uses
metres; the declared STEP unit is retained as source provenance.

This adapter reports Gmsh observations; it does not infer physical meaning
from face orientation, names, or the order in which CAD entities happen to be
returned.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import re
import tempfile
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from febio_cae.domain import FrameId, unit_definition
from febio_cae.domain.canonical import canonical_bytes

from .backend import (
    BACKEND_TET10_ORDER_ID,
    BACKEND_TET10_TO_CANONICAL_POSITIONS,
    BackendBody,
    BackendElement,
    BackendError,
    BackendErrorCategory,
    BackendFace,
    BackendInspection,
    BackendMesh,
    BackendMeshFace,
    BackendNode,
)

_STEP_TEXT_ENCODING = "latin-1"
_SI_LENGTH_RE = re.compile(
    r"SI_UNIT\s*\(\s*(?P<prefix>\$|\.[A-Z]+\.)\s*,\s*\.METRE\.\s*\)",
    re.IGNORECASE,
)
_CONVERSION_LENGTH_RE = re.compile(
    r"CONVERSION_BASED_UNIT\s*\(\s*'(?P<name>[^']+)'(?P<body>[^;]*)LENGTH_UNIT",
    re.IGNORECASE | re.DOTALL,
)
_BODY_ID_RE = re.compile(r"^body-(?P<tag>[1-9][0-9]*)$")
_TET10_TYPE = 11
# Native Gmsh state is process-global even across different backend/module
# wrappers. Non-reentrant admission avoids both query/initialize races and
# waiting indefinitely on nested calls. External callers must still coordinate.
_GMSH_ADMISSION = threading.Lock()
# OCC otherwise defaults to millimetres (and that default has changed across
# historical Gmsh releases). Keep the source STEP unit as provenance, but
# make the native coordinate unit deterministic before importing the shape.
_OCC_TARGET_UNIT = "M"
_OCC_TARGET_SCALE_TO_SI = 1.0
_LENGTH_PREFIXES = {
    "$": "m",
    ".MILLI.": "mm",
}
_CONVERSION_LENGTH_NAMES = {
    "m": "m",
    "meter": "m",
    "metre": "m",
    "millimeter": "mm",
    "millimetre": "mm",
    "mm": "mm",
}


@dataclass(frozen=True, slots=True)
class GmshOCCConfig:
    """Explicit native-module, version, kernel, and frame configuration."""

    module_name: str = "gmsh"
    expected_version: str = "4.15.2"
    geometry_kernel: str = "OpenCASCADE"
    frame_id: str = "World"
    expected_occt_version: str | None = None
    require_step_ap214: bool = False
    cpu_workers: int | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.module_name, "module_name"),
            (self.expected_version, "expected_version"),
            (self.geometry_kernel, "geometry_kernel"),
            (self.frame_id, "frame_id"),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(
                    f"{field_name} must be non-empty text without surrounding whitespace"
                )
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError(f"{field_name} contains a control character")
        FrameId(self.frame_id)
        if self.expected_occt_version is not None:
            value = self.expected_occt_version
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError("expected_occt_version must be non-empty, unpadded text")
        if type(self.require_step_ap214) is not bool:
            raise ValueError("require_step_ap214 must be a bool")
        if self.cpu_workers is not None and (
            type(self.cpu_workers) is not int or self.cpu_workers <= 0
        ):
            raise ValueError("cpu_workers must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class _BodyContext:
    body_id: str
    volume_tag: int
    faces: tuple[BackendFace, ...]
    volume_si: float
    closed_solid: bool


@dataclass(frozen=True, slots=True)
class _ElementContext:
    element_id: int
    backend_node_ids: tuple[int, ...]
    canonical_node_ids: tuple[int, ...]


class GmshOCCBackend:
    """Concrete, optional Gmsh 4.x/OpenCASCADE backend.

    ``GmshOCCBackend`` is safe to import when Gmsh is not installed.  Missing
    modules, version mismatches, unsupported kernels, malformed STEP units,
    and unsupported mesh mappings become :class:`BackendError` values instead
    of being silently replaced by a synthetic implementation.
    """

    backend_id = "gmsh-occ"

    def __init__(self, config: GmshOCCConfig | None = None) -> None:
        if config is not None and not isinstance(config, GmshOCCConfig):
            raise TypeError("config must be a GmshOCCConfig or None")
        self.config = config or GmshOCCConfig()

    @property
    def backend_version(self) -> str:
        return self.config.expected_version

    def inspect(self, content: bytes, requested_body_ids: Sequence[str]) -> BackendInspection:
        source_digest = _source_digest(content)
        if self.config.require_step_ap214:
            _require_ap214_header(content)
        gmsh = self._load_module()
        declared_unit, _declared_scale_to_si = _declared_length_unit(content)
        requested = _requested_body_ids(requested_body_ids)
        with _GmshSession(gmsh, self.config.geometry_kernel) as session:
            self._prepare_owned_session(gmsh)
            session.write(content)
            self._import_step(gmsh, session.path)
            contexts = self._inspect_contexts(gmsh, _OCC_TARGET_SCALE_TO_SI)
        body_ids = {context.body_id for context in contexts}
        unknown = sorted(set(requested).difference(body_ids))
        if unknown:
            raise BackendError(
                BackendErrorCategory.INVALID_INPUT,
                f"requested body is not present in STEP: {unknown[0]!r}",
            )
        bodies = tuple(
            BackendBody(
                context.body_id,
                context.closed_solid,
                context.volume_si,
                context.faces,
                defects=() if context.closed_solid else ("open-boundary",),
            )
            for context in contexts
        )
        return BackendInspection(
            source_digest=source_digest,
            geometry_digest=_geometry_digest(self.config.frame_id, declared_unit, bodies),
            declared_units=(declared_unit,),
            frame=FrameId(self.config.frame_id),
            bodies=bodies,
        )

    def mesh(self, content: bytes, body_id: str, global_size_si: float) -> BackendMesh:
        source_digest = _source_digest(content)
        if self.config.require_step_ap214:
            _require_ap214_header(content)
        gmsh = self._load_module()
        declared_unit, _declared_scale_to_si = _declared_length_unit(content)
        if not isinstance(body_id, str) or not body_id or body_id != body_id.strip():
            raise BackendError(BackendErrorCategory.INVALID_INPUT, "body_id must be non-empty text")
        if isinstance(global_size_si, bool) or not isinstance(global_size_si, (int, float)):
            raise BackendError(BackendErrorCategory.INVALID_INPUT, "global_size_si must be numeric")
        if not math.isfinite(float(global_size_si)) or float(global_size_si) <= 0.0:
            raise BackendError(
                BackendErrorCategory.INVALID_INPUT, "global_size_si must be positive and finite"
            )

        with _GmshSession(gmsh, self.config.geometry_kernel) as session:
            self._prepare_owned_session(gmsh)
            session.write(content)
            self._import_step(gmsh, session.path)
            contexts = self._inspect_contexts(gmsh, _OCC_TARGET_SCALE_TO_SI)
            context = next((item for item in contexts if item.body_id == body_id), None)
            if context is None:
                raise BackendError(
                    BackendErrorCategory.INVALID_INPUT,
                    f"requested body is not present in STEP: {body_id!r}",
                )
            geometry_digest = _geometry_digest(
                self.config.frame_id,
                declared_unit,
                tuple(
                    BackendBody(
                        item.body_id,
                        item.closed_solid,
                        item.volume_si,
                        item.faces,
                        defects=() if item.closed_solid else ("open-boundary",),
                    )
                    for item in contexts
                ),
            )
            if not context.closed_solid:
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    f"body {body_id!r} is not a closed solid",
                )
            return self._mesh_context(
                gmsh,
                context,
                source_digest,
                geometry_digest,
                _OCC_TARGET_SCALE_TO_SI,
                float(global_size_si),
            )

    def _prepare_owned_session(self, gmsh: Any) -> None:
        # Called only after exclusive session admission and initialization.
        expected = self.config.expected_occt_version
        if expected is not None:
            try:
                build_info = gmsh.option.getString("General.BuildInfo")
            except (AttributeError, RuntimeError, TypeError, ValueError, OSError) as error:
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    "Gmsh OCCT build version evidence is unavailable",
                ) from error
            # General.BuildInfo uses semicolon-separated labelled fields, with
            # the native OpenCASCADE version labelled 'OCC version'. Never use
            # build options, module attributes, or a separately installed OCCT.
            versions = (
                [
                    field.partition(":")[2].strip()
                    for field in build_info.split(";")
                    if field.partition(":")[0].strip() == "OCC version"
                ]
                if isinstance(build_info, str)
                else []
            )
            if len(versions) != 1 or versions[0] != expected:
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    f"Gmsh OCCT build version is absent, ambiguous, or does not match {expected!r}",
                )
        if self.config.cpu_workers is not None:
            try:
                gmsh.option.setNumber("General.NumThreads", self.config.cpu_workers)
            except (AttributeError, RuntimeError, TypeError, ValueError, OSError) as error:
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    "Gmsh could not set the configured CPU worker count",
                ) from error

    def _load_module(self) -> Any:
        try:
            module = importlib.import_module(self.config.module_name)
        except (ImportError, OSError, RuntimeError) as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT,
                f"configured Gmsh module is unavailable: {self.config.module_name!r}",
            ) from error
        actual_version = _module_version(module)
        if actual_version != self.config.expected_version:
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                f"Gmsh version {actual_version!r} does not match configured {self.config.expected_version!r}",
            )
        return module

    def _import_step(self, gmsh: Any, path: Path) -> None:
        if self.config.geometry_kernel != "OpenCASCADE":
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                f"unsupported geometry kernel: {self.config.geometry_kernel!r}",
            )
        try:
            option = getattr(gmsh, "option", None)
            set_string = getattr(option, "setString", None)
            if not callable(set_string):
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    "configured Gmsh module has no string option API for OCC target units",
                )
            # This must precede importShapes: the option controls OCC's STEP
            # coordinate conversion, not a post-import mesh transformation.
            set_string("Geometry.OCCTargetUnit", _OCC_TARGET_UNIT)
            gmsh.model.add("febio_cae_step")
            occ = gmsh.model.occ
            if not hasattr(occ, "importShapes"):
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    "configured Gmsh module has no OpenCASCADE importShapes API",
                )
            occ.importShapes(str(path))
            occ.synchronize()
        except BackendError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, f"Gmsh STEP import failed: {error}"
            ) from error

    def _inspect_contexts(self, gmsh: Any, native_scale_to_si: float) -> tuple[_BodyContext, ...]:
        try:
            entities = _dimtags(gmsh.model.getEntities(3))
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, f"Gmsh volume inspection failed: {error}"
            ) from error
        if not entities:
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                "STEP import produced no 3D volume entities",
            )
        contexts: list[_BodyContext] = []
        for _, volume_tag in sorted(entities):
            body_id = f"body-{volume_tag}"
            faces = self._inspect_faces(gmsh, body_id, volume_tag, native_scale_to_si)
            if not faces:
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    f"volume {body_id!r} has no boundary surfaces",
                )
            # Trigger the mass call here, so an invalid/non-positive volume is
            # diagnosed during inspection rather than much later in MeshingPort.
            volume = _mass(gmsh, 3, volume_tag) * native_scale_to_si**3
            if volume <= 0.0:
                raise BackendError(
                    BackendErrorCategory.INTEGRITY,
                    f"Gmsh returned a non-positive volume for {body_id!r}",
                )
            contexts.append(
                _BodyContext(
                    body_id,
                    volume_tag,
                    faces,
                    volume,
                    _is_closed_solid(
                        gmsh,
                        volume_tag,
                        tuple(_surface_tag(face.face_id) for face in faces),
                    ),
                )
            )
        return tuple(contexts)

    def _inspect_faces(
        self, gmsh: Any, body_id: str, volume_tag: int, native_scale_to_si: float
    ) -> tuple[BackendFace, ...]:
        surface_tags = _boundary_surface_tags(gmsh, volume_tag)
        if not surface_tags:
            return ()
        frame = FrameId(self.config.frame_id)
        faces: list[BackendFace] = []
        for surface_tag in surface_tags:
            try:
                area = _mass(gmsh, 2, surface_tag) * native_scale_to_si**2
                center = _center_of_mass(gmsh, 2, surface_tag)
            except BackendError:
                raise
            except (AttributeError, RuntimeError, TypeError, ValueError) as error:
                raise BackendError(
                    BackendErrorCategory.ENVIRONMENT,
                    f"Gmsh surface measurement failed for {surface_tag}: {error}",
                ) from error
            if area <= 0.0:
                raise BackendError(
                    BackendErrorCategory.INTEGRITY,
                    f"Gmsh returned a non-positive area for surface {surface_tag}",
                )
            face_id = f"{body_id}:face-{surface_tag}"
            faces.append(
                BackendFace(
                    face_id=face_id,
                    body_id=body_id,
                    frame=frame,
                    area_si=area,
                    centroid_si=_scale_point(center, native_scale_to_si),
                    attributes=_entity_attributes(gmsh, 2, surface_tag),
                )
            )
        return tuple(faces)

    def _mesh_context(
        self,
        gmsh: Any,
        context: _BodyContext,
        source_digest: str,
        geometry_digest: str,
        native_scale_to_si: float,
        global_size_si: float,
    ) -> BackendMesh:
        try:
            mesh_api = gmsh.model.mesh
            # OCC import is explicitly normalized to metres. The native size
            # conversion remains expressed in terms of this boundary scale so
            # the unit contract is visible at the point of the native call.
            native_size = global_size_si / native_scale_to_si
            if not hasattr(mesh_api, "setOrder"):
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    "configured Gmsh module has no second-order mesh API",
                )
            set_size = getattr(mesh_api, "setSize", None)
            get_entities = getattr(gmsh.model, "getEntities", None)
            used_point_sizes = False
            if callable(set_size) and callable(get_entities):
                point_entities = _dimtags(get_entities(0))
                if point_entities:
                    set_size(list(point_entities), native_size)
                    used_point_sizes = True
            if (
                not used_point_sizes
                and hasattr(gmsh, "option")
                and hasattr(gmsh.option, "setNumber")
            ):
                gmsh.option.setNumber("Mesh.MeshSizeMax", native_size)
                gmsh.option.setNumber("Mesh.MeshSizeMin", native_size)
            mesh_api.generate(3)
            mesh_api.setOrder(2)
        except BackendError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, f"Gmsh Tet10 generation failed: {error}"
            ) from error

        node_coordinates = _node_coordinates(gmsh, native_scale_to_si)
        elements = self._tet10_elements(gmsh, context.volume_tag, node_coordinates)
        faces = self._mesh_faces(gmsh, context, elements, node_coordinates)
        nodes = tuple(
            BackendNode(node_id, node_coordinates[node_id])
            for node_id in sorted(
                {node_id for element in elements for node_id in element.backend_node_ids}
            )
        )
        return BackendMesh(
            source_digest=source_digest,
            geometry_digest=geometry_digest,
            frame=FrameId(self.config.frame_id),
            body_id=context.body_id,
            nodes=nodes,
            elements=tuple(
                BackendElement(
                    element.element_id,
                    "tet10",
                    element.backend_node_ids,
                    context.body_id,
                    BACKEND_TET10_ORDER_ID,
                )
                for element in elements
            ),
            faces=faces,
            ordering_id=BACKEND_TET10_ORDER_ID,
        )

    def _tet10_elements(
        self, gmsh: Any, volume_tag: int, node_coordinates: dict[int, tuple[float, float, float]]
    ) -> tuple[_ElementContext, ...]:
        try:
            element_types, element_tags, node_tags = gmsh.model.mesh.getElements(3, volume_tag)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, f"Gmsh volume mesh read failed: {error}"
            ) from error
        types = _as_ints(element_types)
        tags_by_type = [_as_ints(item) for item in element_tags]
        nodes_by_type = [_as_ints(item) for item in node_tags]
        if not types or len(types) != len(tags_by_type) or len(types) != len(nodes_by_type):
            raise BackendError(
                BackendErrorCategory.INTEGRITY, "Gmsh element arrays are inconsistent"
            )
        for type_id in types:
            _require_tet10_properties(gmsh, type_id)
            if type_id != _TET10_TYPE:
                raise BackendError(
                    BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                    f"Gmsh returned unsupported 3D element type {type_id}",
                )
        contexts: list[_ElementContext] = []
        for tags, flat_nodes in zip(tags_by_type, nodes_by_type, strict=True):
            if len(flat_nodes) != len(tags) * 10:
                raise BackendError(
                    BackendErrorCategory.INTEGRITY, "Gmsh Tet10 connectivity length is inconsistent"
                )
            for index, element_id in enumerate(tags):
                backend_nodes = tuple(flat_nodes[index * 10 : (index + 1) * 10])
                if len(set(backend_nodes)) != 10 or any(
                    node_id not in node_coordinates for node_id in backend_nodes
                ):
                    raise BackendError(
                        BackendErrorCategory.INTEGRITY, "Gmsh Tet10 references invalid nodes"
                    )
                canonical_nodes = tuple(
                    backend_nodes[position] for position in BACKEND_TET10_TO_CANONICAL_POSITIONS
                )
                from .quadratic_quality import require_positive_quadratic_mapping

                try:
                    require_positive_quadratic_mapping(
                        tuple(node_coordinates[node_id] for node_id in canonical_nodes)
                    )
                except ValueError as error:
                    raise BackendError(BackendErrorCategory.QUALITY, str(error)) from error
                contexts.append(_ElementContext(element_id, backend_nodes, canonical_nodes))
        if not contexts:
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                "Gmsh produced no Tet10 volume elements",
            )
        return tuple(sorted(contexts, key=lambda item: item.element_id))

    def _mesh_faces(
        self,
        gmsh: Any,
        context: _BodyContext,
        elements: Sequence[_ElementContext],
        node_coordinates: dict[int, tuple[float, float, float]],
    ) -> tuple[BackendMeshFace, ...]:
        by_key: dict[tuple[int, int, int], list[tuple[int, int]]] = {}
        for element in elements:
            for local_face_id, positions in enumerate(_tet10_face_positions()):
                key = _sorted_triple(
                    tuple(element.canonical_node_ids[position] for position in positions[:3])
                )
                by_key.setdefault(key, []).append((element.element_id, local_face_id))

        result: list[BackendMeshFace] = []
        for cad_face in context.faces:
            surface_tag = _surface_tag(cad_face.face_id)
            surface_keys = _surface_mesh_keys(gmsh, surface_tag)
            if not surface_keys:
                raise BackendError(
                    BackendErrorCategory.INTEGRITY,
                    f"Gmsh produced no surface facets for CAD face {cad_face.face_id!r}",
                )
            for facet_index, key in enumerate(sorted(surface_keys)):
                adjacency = tuple(sorted(by_key.get(key, ())))
                if not adjacency or len(adjacency) > 2:
                    raise BackendError(
                        BackendErrorCategory.INTEGRITY,
                        f"CAD face {cad_face.face_id!r} has an invalid Tet10 boundary adjacency",
                    )
                corner_points = tuple(node_coordinates[node_id] for node_id in key)
                area, centroid = _triangle_measure(corner_points)
                result.append(
                    BackendMeshFace(
                        face_id=f"{cad_face.face_id}:facet-{facet_index}",
                        adjacent_element_ids=tuple(item[0] for item in adjacency),
                        local_face_ids=tuple(item[1] for item in adjacency),
                        area_si=area,
                        centroid_si=centroid,
                        boundary_points_si=corner_points,
                        source_face_id=cad_face.face_id,
                    )
                )
        if not result:
            raise BackendError(
                BackendErrorCategory.INTEGRITY, "Gmsh produced no boundary mesh faces"
            )
        return tuple(result)


class _GmshSession:
    """Small context manager that gives each backend operation isolated state."""

    def __init__(self, gmsh: Any, kernel: str) -> None:
        self.gmsh = gmsh
        self.kernel = kernel
        self._started = False
        self._admitted = False
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path = Path()

    def __enter__(self) -> Self:
        if self.kernel != "OpenCASCADE":
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                f"unsupported geometry kernel: {self.kernel!r}",
            )
        if not _GMSH_ADMISSION.acquire(blocking=False):
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, "Gmsh session busy; admission refused"
            )
        self._admitted = True
        try:
            if not callable(getattr(self.gmsh, "isInitialized", None)):
                raise BackendError(
                    BackendErrorCategory.ENVIRONMENT, "Gmsh session ownership query unavailable"
                )
            if self.gmsh.isInitialized():
                raise BackendError(
                    BackendErrorCategory.ENVIRONMENT,
                    "Gmsh initialized session is unowned; refusing mutation",
                )
            self._started = True
            self.gmsh.initialize()
            if hasattr(self.gmsh, "option") and hasattr(self.gmsh.option, "setNumber"):
                self.gmsh.option.setNumber("General.Terminal", 0)
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="febio-cae-gmsh-")
            self.path = Path(self._temporary_directory.name) / "input.step"
            return self
        except BackendError:
            self._close()
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            self._close()
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, f"Gmsh session initialization failed: {error}"
            ) from error
        except BaseException:
            self._close()
            raise

    def write(self, content: bytes) -> None:
        try:
            self.path.write_bytes(content)
        except OSError as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, f"temporary STEP write failed: {error}"
            ) from error

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._close()

    def _close(self) -> None:
        try:
            try:
                if self._started and hasattr(self.gmsh, "clear"):
                    self.gmsh.clear()
            finally:
                if self._started and hasattr(self.gmsh, "finalize"):
                    self.gmsh.finalize()
        finally:
            self._started = False
            try:
                if self._temporary_directory is not None:
                    self._temporary_directory.cleanup()
            finally:
                self._temporary_directory = None
                if self._admitted:
                    self._admitted = False
                    _GMSH_ADMISSION.release()


def _source_digest(content: bytes) -> str:
    if not isinstance(content, bytes) or not content:
        raise BackendError(
            BackendErrorCategory.INVALID_INPUT, "STEP content must be non-empty bytes"
        )
    return hashlib.sha256(content).hexdigest()


def _require_ap214_header(content: bytes) -> None:
    """Read HEADER records, without treating comments or string contents as code.

    This is a schema admission check, not a full STEP DATA validator. Native
    import remains responsible for validating the shape representation.
    """
    text = content.decode(_STEP_TEXT_ENCODING)
    token_re = re.compile(r"\s+|/\*.*?\*/|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_-]*|[();,]", re.DOTALL)
    position = 0

    def token() -> str:
        nonlocal position
        while position < len(text):
            match = token_re.match(text, position)
            if match is None:
                break
            position = match.end()
            value = match.group()
            if value.isspace() or value.startswith("/*"):
                continue
            return value if value.startswith("'") else value.upper()
        raise BackendError(BackendErrorCategory.INVALID_INPUT, "malformed STEP HEADER")

    def require(expected: str) -> None:
        if token() != expected:
            raise BackendError(BackendErrorCategory.INVALID_INPUT, "malformed STEP HEADER")

    require("ISO-10303-21")
    require(";")
    require("HEADER")
    require(";")
    schemas: list[list[str]] = []
    while (name := token()) != "ENDSEC":
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) is None:
            raise BackendError(BackendErrorCategory.INVALID_INPUT, "malformed STEP HEADER record")
        require("(")
        depth = 1
        values = []
        while depth:
            value = token()
            if value == ";":
                raise BackendError(
                    BackendErrorCategory.INVALID_INPUT, "malformed STEP HEADER record"
                )
            depth += (value == "(") - (value == ")")
            if depth:
                values.append(value)
        require(";")
        if name == "FILE_SCHEMA":
            schemas.append(values)
    require(";")
    require("DATA")
    require(";")
    if len(schemas) != 1:
        raise BackendError(
            BackendErrorCategory.INVALID_INPUT, "STEP HEADER must declare exactly one FILE_SCHEMA"
        )
    values = schemas[0]
    if len(values) != 3 or values[0] != "(" or values[2] != ")":
        raise BackendError(BackendErrorCategory.INVALID_INPUT, "ambiguous STEP FILE_SCHEMA")
    # AP214's standard schema identifier may carry its ISO edition identifier.
    if (
        re.fullmatch(
            r"'AUTOMOTIVE_DESIGN(?:\s+\{\s*1\s+0\s+10303\s+214\s+[123]\s+1\s+1\s*\})?'",
            values[1],
            re.IGNORECASE,
        )
        is None
    ):
        raise BackendError(
            BackendErrorCategory.UNSUPPORTED_CAPABILITY, "STEP FILE_SCHEMA is not AP214"
        )


def _declared_length_unit(content: bytes) -> tuple[str, float]:
    if not isinstance(content, bytes) or not content:
        raise BackendError(
            BackendErrorCategory.INVALID_INPUT, "STEP content must be non-empty bytes"
        )
    text = content.decode(_STEP_TEXT_ENCODING)
    candidates: list[str] = []
    for match in _SI_LENGTH_RE.finditer(text):
        prefix = match.group("prefix").upper()
        candidates.append(_LENGTH_PREFIXES.get(prefix, f"si:{prefix.lower()}"))
    for match in _CONVERSION_LENGTH_RE.finditer(text):
        name = " ".join(match.group("name").strip().casefold().split())
        candidates.append(_CONVERSION_LENGTH_NAMES.get(name, f"conversion:{name}"))
    if not candidates:
        if re.search(r"LENGTH_UNIT", text, re.IGNORECASE):
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY, "STEP length unit is not supported"
            )
        raise BackendError(BackendErrorCategory.INVALID_INPUT, "STEP has no declared length unit")
    unique = tuple(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise BackendError(
            BackendErrorCategory.INVALID_INPUT, "STEP contains conflicting length units"
        )
    try:
        definition = unit_definition(unique[0])
    except (TypeError, ValueError) as error:
        raise BackendError(
            BackendErrorCategory.UNSUPPORTED_CAPABILITY,
            f"STEP length unit is unsupported: {unique[0]!r}",
        ) from error
    if (
        definition.dimension.length != 1
        or definition.dimension.mass != 0
        or definition.dimension.time != 0
    ):
        raise BackendError(
            BackendErrorCategory.UNSUPPORTED_CAPABILITY,
            f"STEP unit is not a length unit: {unique[0]!r}",
        )
    return unique[0], definition.scale_to_si


def _module_version(module: Any) -> str:
    value = getattr(module, "__version__", None)
    if isinstance(value, str) and value:
        return value
    version_function = getattr(module, "version", None)
    if callable(version_function):
        try:
            value = version_function()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            value = None
        if isinstance(value, (tuple, list)):
            value = ".".join(str(item) for item in value)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _requested_body_ids(value: Sequence[str]) -> tuple[str, ...]:
    result = tuple(value)
    if any(not isinstance(item, str) or not item or item != item.strip() for item in result):
        raise BackendError(
            BackendErrorCategory.INVALID_INPUT, "requested_body_ids must contain valid text"
        )
    if len(set(result)) != len(result):
        raise BackendError(
            BackendErrorCategory.INVALID_INPUT, "requested_body_ids must not contain duplicates"
        )
    return result


def _dimtags(value: Iterable[Sequence[int]]) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for item in value:
        if len(item) != 2:
            raise BackendError(
                BackendErrorCategory.INTEGRITY, "Gmsh entity tag is not a dimension/tag pair"
            )
        dimension, tag = int(item[0]), abs(int(item[1]))
        if dimension < 0 or tag <= 0:
            raise BackendError(
                BackendErrorCategory.INTEGRITY, "Gmsh returned an invalid entity tag"
            )
        result.append((dimension, tag))
    return tuple(result)


def _as_ints(value: Iterable[Any]) -> list[int]:
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError) as error:
        raise BackendError(
            BackendErrorCategory.INTEGRITY, "Gmsh returned non-integer tags"
        ) from error


def _mass(gmsh: Any, dimension: int, tag: int) -> float:
    try:
        value = float(gmsh.model.occ.getMass(dimension, tag))
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError) as error:
        raise BackendError(
            BackendErrorCategory.ENVIRONMENT,
            f"Gmsh mass query failed for ({dimension}, {tag}): {error}",
        ) from error
    if not math.isfinite(value):
        raise BackendError(
            BackendErrorCategory.INTEGRITY,
            f"Gmsh mass query was non-finite for ({dimension}, {tag})",
        )
    return value


def _center_of_mass(gmsh: Any, dimension: int, tag: int) -> tuple[float, float, float]:
    try:
        value = tuple(float(item) for item in gmsh.model.occ.getCenterOfMass(dimension, tag))
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError) as error:
        raise BackendError(
            BackendErrorCategory.ENVIRONMENT,
            f"Gmsh centroid query failed for ({dimension}, {tag}): {error}",
        ) from error
    if len(value) != 3 or any(not math.isfinite(item) for item in value):
        raise BackendError(
            BackendErrorCategory.INTEGRITY, f"Gmsh centroid was invalid for ({dimension}, {tag})"
        )
    return value  # type: ignore[return-value]


def _scale_point(point: Sequence[float], scale: float) -> tuple[float, float, float]:
    return (point[0] * scale, point[1] * scale, point[2] * scale)


def _boundary_surface_tags(gmsh: Any, volume_tag: int) -> tuple[int, ...]:
    try:
        try:
            boundary = gmsh.model.getBoundary([(3, volume_tag)], False, False)
        except TypeError:
            boundary = gmsh.model.getBoundary([(3, volume_tag)])
        dimtags = _dimtags(boundary)
    except BackendError:
        raise
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        raise BackendError(
            BackendErrorCategory.ENVIRONMENT,
            f"Gmsh boundary query failed for volume {volume_tag}: {error}",
        ) from error
    return tuple(sorted({tag for dimension, tag in dimtags if dimension == 2}))


def _entity_attributes(gmsh: Any, dimension: int, tag: int) -> tuple[str, ...]:
    names: set[str] = set()
    get_name = getattr(gmsh.model, "getEntityName", None)
    if callable(get_name):
        try:
            name = get_name(dimension, tag)
        except (RuntimeError, TypeError, ValueError):
            name = ""
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    get_groups = getattr(gmsh.model, "getPhysicalGroupsForEntity", None)
    get_group_name = getattr(gmsh.model, "getPhysicalName", None)
    if callable(get_groups) and callable(get_group_name):
        try:
            groups = _as_ints(get_groups(dimension, tag))
        except (BackendError, RuntimeError, TypeError, ValueError):
            groups = []
        for group_tag in groups:
            try:
                name = get_group_name(dimension, group_tag)
            except (RuntimeError, TypeError, ValueError):
                name = ""
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    return tuple(sorted(names))


def _is_closed_solid(gmsh: Any, volume_tag: int, surface_tags: Sequence[int]) -> bool:
    if not surface_tags:
        return False
    get_adjacencies = getattr(gmsh.model, "getAdjacencies", None)
    if not callable(get_adjacencies):
        # A volume entity with a non-empty OCC boundary is the strongest fact
        # available from older/fake APIs; native Gmsh exposes adjacencies and
        # is checked more strictly below.
        return True
    for surface_tag in surface_tags:
        try:
            adjacency = get_adjacencies(2, surface_tag)
            if not isinstance(adjacency, (tuple, list)) or len(adjacency) < 1:
                return False
            upward = {int(item) for item in adjacency[0]}
            if volume_tag not in upward:
                return False
        except (RuntimeError, TypeError, ValueError):
            return False
    return True


def _geometry_digest(frame_id: str, declared_unit: str, bodies: Sequence[BackendBody]) -> str:
    payload = {
        "schema_version": "1",
        "frame": frame_id,
        "declared_units": [declared_unit],
        "bodies": [body.to_dict() for body in sorted(bodies, key=lambda item: item.body_id)],
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _node_coordinates(gmsh: Any, scale_to_si: float) -> dict[int, tuple[float, float, float]]:
    try:
        values = gmsh.model.mesh.getNodes(-1, -1, True, False)
    except TypeError:
        try:
            values = gmsh.model.mesh.getNodes(-1, -1)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT, f"Gmsh node read failed: {error}"
            ) from error
    except (AttributeError, RuntimeError, ValueError) as error:
        raise BackendError(
            BackendErrorCategory.ENVIRONMENT, f"Gmsh node read failed: {error}"
        ) from error
    if not isinstance(values, (tuple, list)) or len(values) < 2:
        raise BackendError(
            BackendErrorCategory.INTEGRITY, "Gmsh node API returned an invalid record"
        )
    node_ids = _as_ints(values[0])
    raw = [float(item) for item in values[1]]
    if len(raw) != len(node_ids) * 3:
        raise BackendError(
            BackendErrorCategory.INTEGRITY, "Gmsh node coordinates length is inconsistent"
        )
    coordinates: dict[int, tuple[float, float, float]] = {}
    for index, node_id in enumerate(node_ids):
        point = (raw[index * 3], raw[index * 3 + 1], raw[index * 3 + 2])
        if any(not math.isfinite(item) for item in point):
            raise BackendError(
                BackendErrorCategory.INTEGRITY, "Gmsh returned non-finite node coordinates"
            )
        coordinates[node_id] = _scale_point(point, scale_to_si)
    return coordinates


def _require_tet10_properties(gmsh: Any, type_id: int) -> None:
    try:
        properties = gmsh.model.mesh.getElementProperties(type_id)
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        raise BackendError(
            BackendErrorCategory.ENVIRONMENT,
            f"Gmsh element properties failed for type {type_id}: {error}",
        ) from error
    if not isinstance(properties, (tuple, list)) or len(properties) < 6:
        raise BackendError(
            BackendErrorCategory.INTEGRITY,
            f"Gmsh element properties are incomplete for type {type_id}",
        )
    name, dimension, order, node_count, _, primary_count = properties[:6]
    if (
        type_id != _TET10_TYPE
        or str(name).casefold() not in {"tetrahedron 10", "tetrahedron10"}
        or int(dimension) != 3
        or int(order) != 2
        or int(node_count) != 10
        or int(primary_count) != 4
    ):
        raise BackendError(
            BackendErrorCategory.UNSUPPORTED_CAPABILITY,
            f"Gmsh element type {type_id} is not the supported Tet10 mapping",
        )


def _tet10_face_positions() -> tuple[tuple[int, ...], ...]:
    return (
        (0, 2, 1, 6, 5, 4),
        (0, 1, 3, 4, 8, 7),
        (1, 2, 3, 5, 9, 8),
        (0, 3, 2, 7, 9, 6),
    )


def _surface_mesh_keys(gmsh: Any, surface_tag: int) -> set[tuple[int, int, int]]:
    try:
        element_types, _, node_tags = gmsh.model.mesh.getElements(2, surface_tag)
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        raise BackendError(
            BackendErrorCategory.ENVIRONMENT,
            f"Gmsh surface mesh read failed for {surface_tag}: {error}",
        ) from error
    keys: set[tuple[int, int, int]] = set()
    for type_id_raw, flat_raw in zip(element_types, node_tags, strict=True):
        type_id = int(type_id_raw)
        try:
            properties = gmsh.model.mesh.getElementProperties(type_id)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise BackendError(
                BackendErrorCategory.ENVIRONMENT,
                f"Gmsh surface element properties failed for type {type_id}: {error}",
            ) from error
        if not isinstance(properties, (tuple, list)) or len(properties) < 6:
            raise BackendError(
                BackendErrorCategory.INTEGRITY, "Gmsh surface element properties are incomplete"
            )
        node_count = int(properties[3])
        primary_count = int(properties[5])
        if primary_count != 3 or node_count < 3:
            raise BackendError(
                BackendErrorCategory.UNSUPPORTED_CAPABILITY,
                f"Gmsh surface element type {type_id} is not triangular",
            )
        flat = _as_ints(flat_raw)
        tags = flat
        if len(tags) % node_count:
            raise BackendError(
                BackendErrorCategory.INTEGRITY, "Gmsh surface connectivity length is inconsistent"
            )
        for index in range(0, len(tags), node_count):
            keys.add(_sorted_triple(tuple(tags[index + offset] for offset in range(3))))
    return keys


def _surface_tag(face_id: str) -> int:
    match = re.search(r":face-(?P<tag>[1-9][0-9]*)$", face_id)
    if match is None:
        raise BackendError(
            BackendErrorCategory.INTEGRITY, f"native face ID has no surface tag: {face_id!r}"
        )
    return int(match.group("tag"))


def _sorted_triple(values: Sequence[int]) -> tuple[int, int, int]:
    if len(values) != 3:
        raise BackendError(BackendErrorCategory.INTEGRITY, "a surface key must contain three nodes")
    first, second, third = sorted(values)
    return first, second, third


def _triangle_measure(
    points: Sequence[Sequence[float]],
) -> tuple[float, tuple[float, float, float]]:
    if len(points) != 3:
        raise BackendError(
            BackendErrorCategory.INTEGRITY, "triangle boundary must have three corner points"
        )
    first, second, third = points
    a = (second[0] - first[0], second[1] - first[1], second[2] - first[2])
    b = (third[0] - first[0], third[1] - first[1], third[2] - first[2])
    cross = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    area = 0.5 * math.sqrt(sum(item * item for item in cross))
    if not math.isfinite(area) or area <= 0.0:
        raise BackendError(
            BackendErrorCategory.QUALITY, "Gmsh returned a degenerate surface triangle"
        )
    return area, (
        (first[0] + second[0] + third[0]) / 3.0,
        (first[1] + second[1] + third[1]) / 3.0,
        (first[2] + second[2] + third[2]) / 3.0,
    )


def _signed_volume(points: Sequence[Sequence[float]]) -> float:
    first, second, third, fourth = points
    a = (second[0] - first[0], second[1] - first[1], second[2] - first[2])
    b = (third[0] - first[0], third[1] - first[1], third[2] - first[2])
    c = (fourth[0] - first[0], fourth[1] - first[1], fourth[2] - first[2])
    cross = (b[1] * c[2] - b[2] * c[1], b[2] * c[0] - b[0] * c[2], b[0] * c[1] - b[1] * c[0])
    return (a[0] * cross[0] + a[1] * cross[1] + a[2] * cross[2]) / 6.0


__all__ = ["GmshOCCBackend", "GmshOCCConfig"]
