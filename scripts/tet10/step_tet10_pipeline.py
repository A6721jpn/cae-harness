from __future__ import annotations

import argparse
import json
from pathlib import Path


def _quality_stats(values):
    import numpy as np

    a = np.asarray(values, dtype=float)
    if a.size == 0:
        return {"count": 0}
    return {
        "count": int(a.size),
        "min": float(np.min(a)),
        "p01": float(np.percentile(a, 1)),
        "p05": float(np.percentile(a, 5)),
        "median": float(np.median(a)),
        "mean": float(np.mean(a)),
        "max": float(np.max(a)),
    }


def _tet_signed_volumes(points, tet10):
    import numpy as np

    corners = np.asarray(tet10, dtype=np.int64)[:, :4]
    xyz = np.asarray(points, dtype=float)[corners]
    matrices = np.stack(
        (xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0], xyz[:, 3] - xyz[:, 0]),
        axis=1,
    )
    return np.linalg.det(matrices) / 6.0


def mesh_step(
    source: Path,
    output_dir: Path,
    target_size: float,
    min_size: float,
    algorithm3d: int,
    heal: bool,
    heal_tolerance: float,
    straight_sided: bool,
):
    import gmsh
    import meshio
    import numpy as np

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    route_parts = ["healed" if heal else "direct"]
    if heal:
        route_parts.append(f"tol_{heal_tolerance:g}".replace(".", "p"))
    if straight_sided:
        route_parts.append("straight")
    route = "_".join(route_parts)
    msh_path = output_dir / f"{stem}_{route}_tet10.msh"
    inp_path = output_dir / f"{stem}_{route}_tet10.inp"
    vtk_path = output_dir / f"{stem}_{route}_tet10.vtu"
    report_path = output_dir / f"{stem}_{route}_report.json"

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 1)
    gmsh.logger.start()
    gmsh.model.add(stem)
    imported = gmsh.model.occ.importShapes(
        str(source), highestDimOnly=False, format="step"
    )
    imported_before_heal = list(imported)

    if heal:
        heal_targets = [entity for entity in imported if entity[0] == 3]
        if not heal_targets:
            raise RuntimeError("No solid or volume entity is available for healing")
        imported = gmsh.model.occ.healShapes(
            heal_targets,
            tolerance=heal_tolerance,
            fixDegenerated=True,
            fixSmallEdges=True,
            fixSmallFaces=True,
            sewFaces=True,
            makeSolids=True,
        )

    if len([entity for entity in imported if entity[0] == 3]) > 1:
        gmsh.model.occ.removeAllDuplicates()
    gmsh.model.occ.synchronize()

    entities = {
        str(dim): [tag for _, tag in gmsh.model.getEntities(dim)]
        for dim in range(4)
    }
    if not entities["3"]:
        raise RuntimeError("STEP import did not produce a volume entity")

    bbox = gmsh.model.getBoundingBox(-1, -1)
    volume_masses = {
        str(tag): float(gmsh.model.occ.getMass(3, tag)) for tag in entities["3"]
    }

    gmsh.option.setNumber("Mesh.MeshSizeMin", min_size)
    gmsh.option.setNumber("Mesh.MeshSizeMax", target_size)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 20)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    gmsh.option.setNumber("Mesh.Algorithm3D", algorithm3d)
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)
    gmsh.option.setNumber("Mesh.ElementOrder", 1)
    gmsh.model.mesh.generate(3)
    gmsh.model.mesh.optimize("Netgen")
    first_order_types, first_order_tags, _ = gmsh.model.mesh.getElements(3)
    first_order_counts = {
        str(t): int(len(tags)) for t, tags in zip(first_order_types, first_order_tags)
    }

    gmsh.option.setNumber("Mesh.SecondOrderLinear", 1 if straight_sided else 0)
    gmsh.model.mesh.setOrder(2)
    if not straight_sided:
        gmsh.model.mesh.optimize("HighOrder")
        gmsh.model.mesh.optimize("HighOrderElastic")

    element_types, element_tags, _ = gmsh.model.mesh.getElements(3)
    volume_counts = {
        str(t): int(len(tags)) for t, tags in zip(element_types, element_tags)
    }
    tet10_tags = []
    for element_type, tags in zip(element_types, element_tags):
        name, _, order, num_nodes, _, _ = gmsh.model.mesh.getElementProperties(
            element_type
        )
        if name == "Tetrahedron 10":
            tet10_tags.extend(int(v) for v in tags)
        elif len(tags):
            raise RuntimeError(
                f"Unexpected 3D element type: {name}, order={order}, nodes={num_nodes}"
            )

    if not tet10_tags:
        raise RuntimeError("No Tet10 volume elements were generated")

    gamma = gmsh.model.mesh.getElementQualities(tet10_tags, "gamma")
    min_sicn = gmsh.model.mesh.getElementQualities(tet10_tags, "minSICN")
    min_sige = gmsh.model.mesh.getElementQualities(tet10_tags, "minSIGE")

    gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
    gmsh.write(str(msh_path))

    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    gmsh_report = {
        "source": str(source),
        "route": route,
        "straight_sided_tet10": straight_sided,
        "heal_tolerance_mm": heal_tolerance if heal else None,
        "gmsh_version": gmsh.option.getString("General.Version"),
        "imported_entity_counts_before_heal": {
            str(dim): sum(1 for entity_dim, _ in imported_before_heal if entity_dim == dim)
            for dim in range(4)
        },
        "imported_entity_counts_after_heal": {
            str(dim): sum(1 for entity_dim, _ in imported if entity_dim == dim)
            for dim in range(4)
        },
        "entities": entities,
        "bbox_mm": [float(v) for v in bbox],
        "volume_mm3_by_tag": volume_masses,
        "target_size_mm": target_size,
        "min_size_mm": min_size,
        "algorithm3d": algorithm3d,
        "first_order_volume_element_counts_by_gmsh_type": first_order_counts,
        "second_order_volume_element_counts_by_gmsh_type": volume_counts,
        "node_count": int(len(node_tags)),
        "tet10_count": int(len(tet10_tags)),
        "quality_gamma": _quality_stats(gamma),
        "quality_minSICN": _quality_stats(min_sicn),
        "quality_minSIGE": _quality_stats(min_sige),
        "gmsh_log_tail": gmsh.logger.get()[-80:],
    }
    gmsh.logger.stop()
    gmsh.finalize()

    mesh = meshio.read(msh_path)
    tet10_blocks = [c.data for c in mesh.cells if c.type == "tetra10"]
    if not tet10_blocks:
        raise RuntimeError("Written MSH does not contain tetra10 cells")
    tet10 = np.vstack(tet10_blocks)
    signed = _tet_signed_volumes(mesh.points, tet10)
    abs_volume = np.abs(signed)
    meshio_report = {
        "tet10_connectivity_count": int(len(tet10)),
        "corner_signed_volume_mm3": {
            **_quality_stats(signed),
            "negative_count": int(np.count_nonzero(signed < 0)),
            "zero_or_near_zero_count": int(
                np.count_nonzero(abs_volume <= max(1.0e-12, target_size**3 * 1.0e-12))
            ),
            "absolute_sum": float(np.sum(abs_volume)),
            "signed_sum": float(np.sum(signed)),
        },
    }
    gmsh_report["independent_meshio_check"] = meshio_report

    volume_cells = [c for c in mesh.cells if c.type == "tetra10"]
    volume_only = meshio.Mesh(points=mesh.points, cells=volume_cells)
    meshio.write(inp_path, volume_only, file_format="abaqus")
    abaqus_text = inp_path.read_text(encoding="utf-8")
    if "C3D10MH" not in abaqus_text:
        raise RuntimeError("Expected meshio to write C3D10MH Tet10 elements")
    inp_path.write_text(
        abaqus_text.replace("C3D10MH", "C3D10"), encoding="utf-8", newline="\n"
    )
    meshio.write(vtk_path, volume_only, file_format="vtu")

    report_path.write_text(
        json.dumps(gmsh_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "msh": str(msh_path),
        "inp": str(inp_path),
        "vtu": str(vtk_path),
        "report": str(report_path),
        "summary": gmsh_report,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Import a STEP solid, optionally heal it, and generate Tet10 mesh."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--size", type=float, default=2.0)
    parser.add_argument("--min-size", type=float, default=0.25)
    parser.add_argument("--algorithm3d", type=int, default=10)
    parser.add_argument("--heal", action="store_true")
    parser.add_argument("--heal-tolerance", type=float, default=1.0e-3)
    parser.add_argument(
        "--straight-sided",
        action="store_true",
        help="Keep Tet10 midside nodes at edge midpoints instead of projecting them to CAD.",
    )
    args = parser.parse_args()
    result = mesh_step(
        args.source.resolve(),
        args.output_dir.resolve(),
        args.size,
        args.min_size,
        args.algorithm3d,
        args.heal,
        args.heal_tolerance,
        args.straight_sided,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
