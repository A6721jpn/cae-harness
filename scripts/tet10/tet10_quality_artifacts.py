from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def gmsh_to_meshio_tet10(tet10: np.ndarray) -> np.ndarray:
    """Convert Gmsh type-11 connectivity to meshio's canonical Tet10 order."""
    tet10 = np.asarray(tet10, dtype=np.int64)
    if tet10.ndim != 2 or tet10.shape[1] != 10:
        raise ValueError("tet10 must have shape (element_count, 10)")
    return tet10[:, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]]


def build_quality_records(
    *,
    points: np.ndarray,
    tet10: np.ndarray,
    gmsh_element_tags: np.ndarray,
    min_sicn: np.ndarray,
    gamma: np.ndarray,
    min_sige: np.ndarray,
) -> dict[str, np.ndarray]:
    """Build per-element diagnostics in the same order used by the INP export."""
    points = np.asarray(points, dtype=float)
    tet10 = np.asarray(tet10, dtype=np.int64)
    gmsh_element_tags = np.asarray(gmsh_element_tags, dtype=np.int64)
    min_sicn = np.asarray(min_sicn, dtype=float)
    gamma = np.asarray(gamma, dtype=float)
    min_sige = np.asarray(min_sige, dtype=float)

    element_count = len(tet10)
    arrays = (gmsh_element_tags, min_sicn, gamma, min_sige)
    if any(len(values) != element_count for values in arrays):
        raise ValueError("All quality arrays must have the same element count as tet10")
    if tet10.ndim != 2 or tet10.shape[1] != 10:
        raise ValueError("tet10 must have shape (element_count, 10)")

    corner_xyz = points[tet10[:, :4]]
    centroids = corner_xyz.mean(axis=1)
    matrices = np.stack(
        (
            corner_xyz[:, 1] - corner_xyz[:, 0],
            corner_xyz[:, 2] - corner_xyz[:, 0],
            corner_xyz[:, 3] - corner_xyz[:, 0],
        ),
        axis=1,
    )
    signed_volumes = np.linalg.det(matrices) / 6.0

    quality_band = np.full(element_count, 3, dtype=np.uint8)
    quality_band[min_sicn < 0.10] = 2
    quality_band[min_sicn < 0.05] = 1
    quality_band[min_sicn < 0.01] = 0

    return {
        "centroids": centroids,
        "corner_signed_volume_mm3": signed_volumes,
        "quality_band": quality_band,
        "inp_element_id": np.arange(1, element_count + 1, dtype=np.int64),
        "gmsh_element_tag": gmsh_element_tags,
        "minSICN": min_sicn,
        "gamma": gamma,
        "minSIGE": min_sige,
        "low_quality_mask": min_sicn < 0.10,
    }


def _load_gmsh_tet10(msh_path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    import gmsh

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.open(str(msh_path))

        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        node_tags = np.asarray(node_tags, dtype=np.int64)
        points = np.asarray(coordinates, dtype=float).reshape((-1, 3))

        element_types, element_tag_blocks, node_tag_blocks = gmsh.model.mesh.getElements(3)
        try:
            block_index = list(element_types).index(11)
        except ValueError as exc:
            raise RuntimeError("No Gmsh type 11 (Tet10) volume elements were found") from exc

        element_tags = np.asarray(element_tag_blocks[block_index], dtype=np.int64)
        connectivity_tags = np.asarray(node_tag_blocks[block_index], dtype=np.int64).reshape((-1, 10))

        max_tag = int(node_tags.max())
        node_index = np.full(max_tag + 1, -1, dtype=np.int64)
        node_index[node_tags] = np.arange(len(node_tags), dtype=np.int64)
        if int(connectivity_tags.max()) > max_tag:
            raise RuntimeError("Element connectivity references an unknown node tag")
        tet10 = node_index[connectivity_tags]
        if np.any(tet10 < 0):
            raise RuntimeError("Element connectivity references an unknown node tag")

        quality = {
            name: np.asarray(
                gmsh.model.mesh.getElementQualities(element_tags.tolist(), name),
                dtype=float,
            )
            for name in ("minSICN", "gamma", "minSIGE")
        }
        return points, tet10, {"element_tags": element_tags, **quality}
    finally:
        gmsh.finalize()


def _stats(values: np.ndarray) -> dict[str, float | int]:
    values = np.asarray(values, dtype=float)
    return {
        "count": int(values.size),
        "min": float(np.min(values)),
        "p01": float(np.percentile(values, 1)),
        "p05": float(np.percentile(values, 5)),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "max": float(np.max(values)),
    }


def write_quality_artifacts(msh_path: Path, output_dir: Path) -> dict[str, Path]:
    import meshio

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = msh_path.stem.removesuffix("_tet10")
    csv_path = output_dir / f"{stem}_low_quality_elements.csv"
    vtu_path = output_dir / f"{stem}_quality.vtu"
    summary_path = output_dir / f"{stem}_quality_summary.json"

    points, tet10, raw = _load_gmsh_tet10(msh_path)
    records = build_quality_records(
        points=points,
        tet10=tet10,
        gmsh_element_tags=raw["element_tags"],
        min_sicn=raw["minSICN"],
        gamma=raw["gamma"],
        min_sige=raw["minSIGE"],
    )

    low = records["low_quality_mask"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "inp_element_id",
                "gmsh_element_tag",
                "minSICN",
                "gamma",
                "minSIGE",
                "centroid_x_mm",
                "centroid_y_mm",
                "centroid_z_mm",
                "corner_signed_volume_mm3",
                "quality_band",
            ]
        )
        for index in np.flatnonzero(low):
            writer.writerow(
                [
                    int(records["inp_element_id"][index]),
                    int(records["gmsh_element_tag"][index]),
                    f"{records['minSICN'][index]:.17g}",
                    f"{records['gamma'][index]:.17g}",
                    f"{records['minSIGE'][index]:.17g}",
                    f"{records['centroids'][index, 0]:.17g}",
                    f"{records['centroids'][index, 1]:.17g}",
                    f"{records['centroids'][index, 2]:.17g}",
                    f"{records['corner_signed_volume_mm3'][index]:.17g}",
                    int(records["quality_band"][index]),
                ]
            )

    mesh = meshio.Mesh(
        points=points,
        cells=[("tetra10", gmsh_to_meshio_tet10(tet10))],
        cell_data={
            name: [records[name]]
            for name in (
                "minSICN",
                "gamma",
                "minSIGE",
                "inp_element_id",
                "gmsh_element_tag",
                "corner_signed_volume_mm3",
                "quality_band",
            )
        },
    )
    meshio.write(vtu_path, mesh, binary=True)

    min_sicn = records["minSICN"]
    low_centroids = records["centroids"][low]
    worst_indices = np.argsort(min_sicn)[:20]
    summary = {
        "source_msh": str(msh_path.resolve()),
        "node_count": int(len(points)),
        "tet10_count": int(len(tet10)),
        "quality_band_definition": {
            "0": "minSICN < 0.01",
            "1": "0.01 <= minSICN < 0.05",
            "2": "0.05 <= minSICN < 0.10",
            "3": "minSICN >= 0.10",
        },
        "counts": {
            "minSICN_nonpositive": int(np.count_nonzero(min_sicn <= 0.0)),
            "minSICN_below_0p01": int(np.count_nonzero(min_sicn < 0.01)),
            "minSICN_below_0p05": int(np.count_nonzero(min_sicn < 0.05)),
            "minSICN_below_0p10": int(np.count_nonzero(min_sicn < 0.10)),
            "corner_volume_nonpositive": int(
                np.count_nonzero(records["corner_signed_volume_mm3"] <= 0.0)
            ),
        },
        "statistics": {
            "minSICN": _stats(records["minSICN"]),
            "gamma": _stats(records["gamma"]),
            "minSIGE": _stats(records["minSIGE"]),
            "corner_signed_volume_mm3": _stats(records["corner_signed_volume_mm3"]),
        },
        "low_quality_centroid_bounds_mm": {
            "minimum": low_centroids.min(axis=0).tolist() if len(low_centroids) else None,
            "maximum": low_centroids.max(axis=0).tolist() if len(low_centroids) else None,
        },
        "worst_20_by_minSICN": [
            {
                "inp_element_id": int(records["inp_element_id"][index]),
                "gmsh_element_tag": int(records["gmsh_element_tag"][index]),
                "minSICN": float(records["minSICN"][index]),
                "gamma": float(records["gamma"][index]),
                "minSIGE": float(records["minSIGE"][index]),
                "centroid_mm": records["centroids"][index].tolist(),
                "corner_signed_volume_mm3": float(
                    records["corner_signed_volume_mm3"][index]
                ),
            }
            for index in worst_indices
        ],
        "outputs": {
            "low_quality_csv": str(csv_path.resolve()),
            "quality_vtu": str(vtu_path.resolve()),
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"csv": csv_path, "vtu": vtu_path, "summary": summary_path}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Tet10 element-quality CSV, VTU, and JSON artifacts."
    )
    parser.add_argument("msh", type=Path, help="Gmsh .msh file containing Tet10 volumes")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (defaults to the .msh parent directory)",
    )
    args = parser.parse_args()
    output_dir = args.output_dir or args.msh.parent
    outputs = write_quality_artifacts(args.msh.resolve(), output_dir.resolve())
    for kind, path in outputs.items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
