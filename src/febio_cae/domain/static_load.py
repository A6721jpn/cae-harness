"""Independent single-solid static intent and consistent quadratic edge loads."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from .artifacts import MeshArtifact
from .canonical import canonical_bytes
from .mesh_policy import SourceLocalRefinementBall
from .units import Quantity


def object_fields(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{name} requires exactly {sorted(fields)}")
    return value


def number(value: Any, name: str, *, positive: bool = False) -> float:
    message = f"{name} must be finite" + (" and positive" if positive else "")
    if type(value) not in (int, float):
        raise ValueError(message)
    try:
        result = float(value)
    except OverflowError:
        raise ValueError(message) from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(message)
    return result


def integer(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 1..{maximum}")
    return value


def quantity(value: Any, unit: str, name: str) -> Quantity:
    item = object_fields(value, {"value", "unit"}, name)
    result = Quantity(number(item["value"], name), item["unit"])
    if result.dimension != Quantity(1, unit).dimension:
        raise ValueError(f"{name} requires {unit}-compatible units")
    return result.to_si()


@dataclass(frozen=True, slots=True)
class EdgeTotalForce:
    curve_id: int
    total_force_n: tuple[float, float, float]

    def __post_init__(self) -> None:
        integer(self.curve_id, "curve_id", 2**31 - 1)
        if len(self.total_force_n) != 3:
            raise ValueError("force vector requires three components")
        vector = tuple(number(value, "force") for value in self.total_force_n)
        if not any(vector):
            raise ValueError("edge total force must be nonzero")
        object.__setattr__(self, "total_force_n", vector)

    @classmethod
    def from_dict(cls, value: Any) -> EdgeTotalForce:
        item = object_fields(
            value, {"curve_id", "semantics", "unit", "frame", "vector"}, "edge load"
        )
        if (item["semantics"], item["unit"], item["frame"]) != ("TOTAL", "N", "World"):
            raise ValueError(
                "edge loads require TOTAL force in N in World; densities are not accepted"
            )
        if not isinstance(item["vector"], list) or len(item["vector"]) != 3:
            raise ValueError("force vector requires three components")
        vector = (
            number(item["vector"][0], "force"),
            number(item["vector"][1], "force"),
            number(item["vector"][2], "force"),
        )
        if not any(vector):
            raise ValueError("edge total force must be nonzero")
        return cls(integer(item["curve_id"], "curve_id", 2**31 - 1), vector)

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "semantics": "TOTAL",
            "unit": "N",
            "frame": "World",
            "vector": list(self.total_force_n),
        }


@dataclass(frozen=True, slots=True)
class StaticLocalRefinement:
    region: SourceLocalRefinementBall
    size: Quantity

    def __post_init__(self) -> None:
        if (
            not isinstance(self.region, SourceLocalRefinementBall)
            or self.region.center.frame.value != "World"
        ):
            raise ValueError("static refinement requires a source World ball")
        if (
            not isinstance(self.size, Quantity)
            or self.size.dimension != Quantity(1, "m").dimension
            or self.size.to_si().value <= 0
        ):
            raise ValueError("static refinement requires a positive length size")
        object.__setattr__(self, "size", self.size.to_si())

    @classmethod
    def from_dict(cls, value: Any) -> StaticLocalRefinement:
        from .codec import _source_local_ball

        item = object_fields(value, {"region", "size"}, "static local refinement")
        region = _source_local_ball(item["region"], "static refinement region")
        size = quantity(item["size"], "m", "refinement size")
        if region.center.frame.value != "World" or size.value <= 0:
            raise ValueError("static refinement requires source World and positive size")
        return cls(region, size)

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region.to_dict(),
            "size": {"value": self.size.to_si().value, "unit": "m"},
        }


@dataclass(frozen=True, slots=True)
class StaticLoadRequest:
    source_sha256: str
    fixed_face_ids: tuple[int, ...]
    loads: tuple[EdgeTotalForce, ...]
    youngs_modulus_pa: float
    poisson_ratio: float
    global_size_m: float
    native_coordinate_unit: str
    algorithm_2d: int
    algorithm_3d: int
    curvature_points: int
    cpu_workers: int
    mesh_wall_seconds: float
    solver_wall_seconds: float
    memory_bytes: int
    max_nodes: int
    max_elements: int
    local_refinements: tuple[StaticLocalRefinement, ...] = ()
    second_order_linear: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None
        ):
            raise ValueError("source_sha256 must be a lowercase SHA-256")
        faces = tuple(self.fixed_face_ids)
        if not faces or len(set(faces)) != len(faces):
            raise ValueError("fixed face IDs must be nonempty and unique")
        for tag in faces:
            integer(tag, "face_id", 2**31 - 1)
        loads = tuple(self.loads)
        if (
            not loads
            or any(not isinstance(load, EdgeTotalForce) for load in loads)
            or len({load.curve_id for load in loads}) != len(loads)
        ):
            raise ValueError("edge loads must be typed, nonempty and unique")
        for field in (
            "youngs_modulus_pa",
            "global_size_m",
            "mesh_wall_seconds",
            "solver_wall_seconds",
        ):
            number(getattr(self, field), field, positive=True)
        if (
            not -1 < number(self.poisson_ratio, "poisson_ratio") < 0.5
            or max(self.mesh_wall_seconds, self.solver_wall_seconds) > 3600
        ):
            raise ValueError("invalid material or wall-time bounds")
        if (
            self.native_coordinate_unit not in ("M", "MM")
            or type(self.algorithm_2d) is not int
            or self.algorithm_2d not in (1, 2, 5, 6, 7, 8, 9, 11)
            or type(self.algorithm_3d) is not int
            or self.algorithm_3d not in (1, 3, 4, 7, 9, 10)
        ):
            raise ValueError("invalid native unit or Gmsh algorithm")
        if type(self.curvature_points) is not int or not 0 <= self.curvature_points <= 1000:
            raise ValueError("invalid curvature_points")
        if type(self.second_order_linear) is not bool:
            raise ValueError("second_order_linear must be a boolean")
        for field, maximum in (
            ("cpu_workers", 256),
            ("memory_bytes", 2**63 - 1),
            ("max_nodes", 10_000_000),
            ("max_elements", 10_000_000),
        ):
            integer(getattr(self, field), field, maximum)
        refinements = tuple(self.local_refinements)
        if len(refinements) > 64 or any(
            not isinstance(refinement, StaticLocalRefinement)
            or refinement.size.value > self.global_size_m
            for refinement in refinements
        ):
            raise ValueError("invalid local refinement bounds")
        object.__setattr__(self, "fixed_face_ids", faces)
        object.__setattr__(self, "loads", loads)
        object.__setattr__(self, "local_refinements", refinements)

    @classmethod
    def from_dict(cls, value: Any) -> StaticLoadRequest:
        item = object_fields(
            value,
            {"schema_version", "source_sha256", "fixed", "loads", "material", "mesh", "budget"},
            "static request",
        )
        if (
            item["schema_version"] != "1"
            or not isinstance(item["source_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"]) is None
        ):
            raise ValueError("static request requires schema 1 and lowercase source SHA-256")
        fixed = object_fields(item["fixed"], {"face_ids", "components", "frame"}, "fixed")
        if fixed["components"] != ["x", "y", "z"] or fixed["frame"] != "World":
            raise ValueError("static support requires explicit World XYZ fixed")
        if not isinstance(fixed["face_ids"], list) or not fixed["face_ids"]:
            raise ValueError("fixed face_ids must be nonempty")
        faces = tuple(integer(v, "face_id", 2**31 - 1) for v in fixed["face_ids"])
        if len(set(faces)) != len(faces):
            raise ValueError("duplicate fixed face")
        if not isinstance(item["loads"], list) or not item["loads"]:
            raise ValueError("loads must be nonempty")
        loads = tuple(EdgeTotalForce.from_dict(v) for v in item["loads"])
        if len({v.curve_id for v in loads}) != len(loads):
            raise ValueError("duplicate loaded curve")
        material = object_fields(
            item["material"], {"model", "youngs_modulus", "poisson_ratio"}, "material"
        )
        if material["model"] != "isotropic_linear_elastic":
            raise ValueError("only isotropic_linear_elastic is supported")
        young = quantity(material["youngs_modulus"], "Pa", "youngs_modulus").value
        nu = quantity(material["poisson_ratio"], "1", "poisson_ratio").value
        if young <= 0 or not -1 < nu < 0.5:
            raise ValueError("invalid elastic material")
        raw_mesh = item["mesh"]
        if not isinstance(raw_mesh, dict):
            raise TypeError("mesh must be an object")
        raw_refinements = raw_mesh.get("local_refinements", [])
        second_order_linear = raw_mesh.get("second_order_linear", False)
        if type(second_order_linear) is not bool:
            raise ValueError("second_order_linear must be a boolean")
        if not isinstance(raw_refinements, list) or len(raw_refinements) > 64:
            raise ValueError("local_refinements must be a list of at most 64 balls")
        refinements = tuple(StaticLocalRefinement.from_dict(v) for v in raw_refinements)
        mesh = object_fields(
            {
                key: value
                for key, value in raw_mesh.items()
                if key not in ("local_refinements", "second_order_linear")
            },
            {
                "global_size",
                "native_coordinate_unit",
                "algorithm_2d",
                "algorithm_3d",
                "curvature_points",
                "max_nodes",
                "max_elements",
            },
            "mesh",
        )
        size = quantity(mesh["global_size"], "m", "global_size").value
        if size <= 0 or mesh["native_coordinate_unit"] not in ("M", "MM"):
            raise ValueError("positive global_size and explicit M/MM native unit required")
        if any(refinement.size.value > size for refinement in refinements):
            raise ValueError("local refinement size exceeds global target")
        a2 = integer(mesh["algorithm_2d"], "algorithm_2d", 11)
        a3 = integer(mesh["algorithm_3d"], "algorithm_3d", 10)
        if a2 not in (1, 2, 5, 6, 7, 8, 9, 11) or a3 not in (1, 3, 4, 7, 9, 10):
            raise ValueError("unsupported Gmsh algorithm choice")
        curvature = mesh["curvature_points"]
        if type(curvature) is not int or not 0 <= curvature <= 1000:
            raise ValueError("curvature_points must be in 0..1000")
        budget = object_fields(
            item["budget"],
            {"cpu_workers", "mesh_wall_seconds", "solver_wall_seconds", "memory_bytes"},
            "budget",
        )
        mesh_time = number(budget["mesh_wall_seconds"], "mesh_wall_seconds", positive=True)
        solver_time = number(budget["solver_wall_seconds"], "solver_wall_seconds", positive=True)
        if max(mesh_time, solver_time) > 3600:
            raise ValueError("wall budget exceeds 3600 seconds")
        return cls(
            item["source_sha256"],
            faces,
            loads,
            young,
            nu,
            size,
            mesh["native_coordinate_unit"],
            a2,
            a3,
            curvature,
            integer(budget["cpu_workers"], "cpu_workers", 256),
            mesh_time,
            solver_time,
            integer(budget["memory_bytes"], "memory_bytes", 2**63 - 1),
            integer(mesh["max_nodes"], "max_nodes", 10_000_000),
            integer(mesh["max_elements"], "max_elements", 10_000_000),
            refinements,
            second_order_linear,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "source_sha256": self.source_sha256,
            "fixed": {
                "face_ids": list(self.fixed_face_ids),
                "components": ["x", "y", "z"],
                "frame": "World",
            },
            "loads": [v.to_dict() for v in self.loads],
            "material": {
                "model": "isotropic_linear_elastic",
                "youngs_modulus": {"value": self.youngs_modulus_pa, "unit": "Pa"},
                "poisson_ratio": {"value": self.poisson_ratio, "unit": "1"},
            },
            "mesh": {
                "global_size": {"value": self.global_size_m, "unit": "m"},
                "native_coordinate_unit": self.native_coordinate_unit,
                "second_order_linear": self.second_order_linear,
                "algorithm_2d": self.algorithm_2d,
                "algorithm_3d": self.algorithm_3d,
                "curvature_points": self.curvature_points,
                "max_nodes": self.max_nodes,
                "max_elements": self.max_elements,
                "local_refinements": [
                    refinement.to_dict() for refinement in self.local_refinements
                ],
            },
            "budget": {
                "cpu_workers": self.cpu_workers,
                "mesh_wall_seconds": self.mesh_wall_seconds,
                "solver_wall_seconds": self.solver_wall_seconds,
                "memory_bytes": self.memory_bytes,
            },
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_bytes(self.to_dict())).hexdigest()

    def selection_digest(self, kind: str, tag: int) -> str:
        return hashlib.sha256(
            canonical_bytes({"source_sha256": self.source_sha256, "kind": kind, "cad_tag": tag})
        ).hexdigest()


def integrate_edge_totals(
    request: StaticLoadRequest,
    mesh: MeshArtifact,
    curves: dict[int, tuple[tuple[int, int, int], ...]],
    fixed_nodes: set[int],
) -> dict[int, tuple[float, float, float]]:
    """Three-point Gauss line3 integration, normalized independently per CAD edge."""
    points = {n.node_id: n.coordinates_si for n in mesh.nodes}
    if set(curves) != {load.curve_id for load in request.loads}:
        raise ValueError("curve sidecar does not cover exactly the declared loads")
    assembled: dict[int, list[float]] = {}
    gauss = ((-math.sqrt(3 / 5), 5 / 9), (0.0, 8 / 9), (math.sqrt(3 / 5), 5 / 9))
    for load in request.loads:
        lines = curves[load.curve_id]
        if not lines or len({tuple(sorted(line[:2])) for line in lines}) != len(lines):
            raise ValueError("empty or duplicated line3 curve")
        weights: dict[int, float] = {}
        length = 0.0
        for line in lines:
            if len(line) != 3 or len(set(line)) != 3 or not set(line) <= points.keys():
                raise ValueError("invalid quadratic curve connectivity")
            if fixed_nodes.intersection(line):
                raise ValueError("loaded curve overlaps fixed support, including midside nodes")
            p = tuple(points[n] for n in line)
            for xi, weight in gauss:
                shape = (0.5 * xi * (xi - 1), 0.5 * xi * (xi + 1), 1 - xi * xi)
                derivative = (xi - 0.5, xi + 0.5, -2 * xi)
                jac = math.sqrt(
                    sum(sum(derivative[j] * p[j][axis] for j in range(3)) ** 2 for axis in range(3))
                )
                if not math.isfinite(jac) or jac <= 0:
                    raise ValueError("degenerate quadratic curve")
                length += weight * jac
                for node, n in zip(line, shape, strict=True):
                    weights[node] = weights.get(node, 0.0) + weight * jac * n
        if not math.isfinite(length) or length <= 0:
            raise ValueError("invalid curve length")
        for node, weight in weights.items():
            force = assembled.setdefault(node, [0.0, 0.0, 0.0])
            for axis in range(3):
                force[axis] += load.total_force_n[axis] * weight / length
    return {node: (force[0], force[1], force[2]) for node, force in assembled.items()}
