"""FEBio XPLT post-processing helpers for von Mises stress and rough FOS."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def von_mises_from_symmetric(stress: np.ndarray) -> np.ndarray:
    """Return von Mises stress for ``[xx, yy, zz, xy, yz, xz]`` tensors."""
    values = np.asarray(stress, dtype=float)
    if values.shape[-1] != 6:
        raise ValueError("stress must have six symmetric-tensor components")
    xx, yy, zz, xy, yz, xz = np.moveaxis(values, -1, 0)
    return np.sqrt(
        0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2)
        + 3.0 * (xy**2 + yz**2 + xz**2)
    )


def rough_safety_factor(
    von_mises: np.ndarray,
    strength_mpa: float = 46.17,
    stress_floor_mpa: float = 0.01,
) -> np.ndarray:
    """Return provisional strength divided by floored von Mises stress."""
    values = np.asarray(von_mises, dtype=float)
    return float(strength_mpa) / np.maximum(values, float(stress_floor_mpa))


def average_element_node_values(
    connectivity: np.ndarray,
    element_node_values: np.ndarray,
    point_count: int,
) -> np.ndarray:
    """Average finite element-node values onto their referenced global nodes."""
    node_ids = np.asarray(connectivity, dtype=np.int64)
    values = np.asarray(element_node_values, dtype=float)
    if node_ids.shape != values.shape:
        raise ValueError("connectivity and element_node_values must have equal shape")

    sums = np.zeros(point_count, dtype=float)
    counts = np.zeros(point_count, dtype=np.int64)
    flat_nodes = node_ids.ravel()
    flat_values = values.ravel()
    finite = np.isfinite(flat_values)
    np.add.at(sums, flat_nodes[finite], flat_values[finite])
    np.add.at(counts, flat_nodes[finite], 1)

    projected = np.full(point_count, np.nan, dtype=float)
    present = counts > 0
    projected[present] = sums[present] / counts[present]
    return projected


def export_final_vtu(
    xplt_path: str | Path,
    output_path: str | Path,
    domain: str = "Part2",
    strength_mpa: float = 46.17,
    stress_floor_mpa: float = 0.01,
) -> dict[str, float | int | str]:
    """Export a domain's final deformed state with persistent VM/FOS arrays."""
    import pyvista as pv
    from pyfebiopt.xplt import xplt

    source = Path(xplt_path)
    destination = Path(output_path)
    if destination.suffix.lower() != ".vtu":
        raise ValueError("output_path must use the .vtu extension")

    plot = xplt(str(source))
    plot.readAllStates()
    if domain not in plot.mesh.parts:
        raise KeyError(f"unknown XPLT domain: {domain}")

    element_rows = np.asarray(plot.mesh.parts[domain], dtype=np.int64)
    element_count = int(element_rows.size)
    nper = np.asarray(plot.mesh.elements.nper, dtype=np.int64)[element_rows]
    element_types = np.asarray(plot.mesh.elements.etype, dtype=object)[element_rows]
    if not np.all(nper == 10) or not np.all(element_types == "ELEM_TET10"):
        raise ValueError(f"domain {domain!r} must contain only Tet10 elements")

    global_connectivity = np.asarray(
        plot.mesh.elements.conn, dtype=np.int64
    )[element_rows, :10]
    used_global_nodes = np.unique(global_connectivity)
    global_to_local = np.full(plot.mesh.nnodes, -1, dtype=np.int64)
    global_to_local[used_global_nodes] = np.arange(used_global_nodes.size)
    local_connectivity = global_to_local[global_connectivity]

    cell_rows = np.column_stack(
        (
            np.full(element_count, 10, dtype=np.int64),
            local_connectivity,
        )
    )
    cell_types = np.full(
        element_count,
        int(pv.CellType.QUADRATIC_TETRA),
        dtype=np.uint8,
    )

    reference_points = np.asarray(plot.mesh.nodes.xyz, dtype=float)[
        used_global_nodes
    ]
    displacement = np.asarray(
        plot.results.node["displacement"].time(-1).comp(":"),
        dtype=float,
    )[used_global_nodes]
    final_points = reference_points + displacement
    grid = pv.UnstructuredGrid(cell_rows.ravel(), cell_types, final_points)

    stress = np.asarray(
        plot.results.elem_item["stress"]
        .domain(domain)
        .time(-1)
        .comp(":"),
        dtype=float,
    )
    element_vm = von_mises_from_symmetric(stress)
    point_vm = average_element_node_values(
        connectivity=local_connectivity,
        element_node_values=np.repeat(element_vm[:, None], 10, axis=1),
        point_count=used_global_nodes.size,
    )
    element_fos = rough_safety_factor(
        element_vm,
        strength_mpa=strength_mpa,
        stress_floor_mpa=stress_floor_mpa,
    )
    point_fos = rough_safety_factor(
        point_vm,
        strength_mpa=strength_mpa,
        stress_floor_mpa=stress_floor_mpa,
    )

    grid.point_data["reference_position_mm"] = reference_points
    grid.point_data["displacement_mm"] = displacement
    grid.point_data["von_Mises_stress_MPa"] = point_vm
    grid.point_data["rough_FOS_46p17MPa"] = point_fos
    grid.cell_data["von_Mises_stress_element_MPa"] = element_vm
    grid.cell_data["rough_FOS_element_46p17MPa"] = element_fos
    grid.cell_data["FEBio_element_row"] = element_rows
    grid.field_data["FEBio_time"] = np.array([float(plot._time[-1])])
    grid.field_data["FOS_strength_basis_MPa"] = np.array([strength_mpa])
    grid.field_data["FOS_stress_floor_MPa"] = np.array([stress_floor_mpa])
    grid.set_active_scalars("von_Mises_stress_MPa", preference="point")

    destination.parent.mkdir(parents=True, exist_ok=True)
    grid.save(destination, binary=True)

    return {
        "source_xplt": str(source),
        "output_vtu": str(destination),
        "domain": domain,
        "time": float(plot._time[-1]),
        "points": int(grid.n_points),
        "elements": int(grid.n_cells),
        "element_vm_max_mpa": float(np.nanmax(element_vm)),
        "element_fos_min": float(np.nanmin(element_fos)),
        "point_vm_max_mpa": float(np.nanmax(point_vm)),
        "point_fos_min": float(np.nanmin(point_fos)),
    }
