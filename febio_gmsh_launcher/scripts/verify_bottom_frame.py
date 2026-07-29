from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from febio_gmsh_launcher.config import load_run_config
from febio_gmsh_launcher.febio_xml import scan_reference_feb
from febio_gmsh_launcher.mesher import mesh_step
from febio_gmsh_launcher.translator import translate_feb


def run_until_initialized(executable: Path, feb: Path, log_path: Path) -> bool:
    process = subprocess.Popen(
        [str(executable), "-i", feb.name],
        cwd=feb.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    initialized = False
    assert process.stdout is not None
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            if "beginning time step" in line:
                initialized = True
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
                break
    process.wait()
    return initialized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-feb", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("[1/4] scanning reference FEB", flush=True)
    reference = scan_reference_feb(args.reference_feb)
    config = load_run_config(args.config, args.reference_feb.stem)
    print(
        f"nodes={len(reference.nodes)} surfaces="
        f"{ {name: len(faces) for name, faces in reference.surfaces.items()} }",
        flush=True,
    )
    print("[2/4] mapping CAD and generating curved Tet10", flush=True)
    mesh = mesh_step(
        config.step_path,
        reference,
        reference.required_surface_names(),
        reference.required_domain_names(),
        config.gmsh,
        config.quality,
        mapping_tolerance=config.gmsh.mapping_tolerance_mm,
    )
    print(
        f"tet10={len(mesh.tet10)} nodes={len(mesh.points)} "
        f"min_G8_detJ={mesh.quality.min_det_j:.17g} "
        f"corrected_nodes={mesh.curvature.corrected_node_count}",
        flush=True,
    )
    output_feb = args.output_dir / "02_Bottom_Frame_Gmsh_Tet10.feb"
    print(f"[3/4] translating {output_feb}", flush=True)
    translate_feb(args.reference_feb, mesh, output_feb)
    report = {
        "reference_feb": str(args.reference_feb.resolve()),
        "step": str(config.step_path),
        "step_sha256": config.step_sha256,
        "nodes": len(mesh.points),
        "tet10": len(mesh.tet10),
        "surfaces": {name: len(faces) for name, faces in mesh.surfaces.items()},
        "domains": {
            name: len(indices)
            for name, indices in mesh.domain_element_indices.items()
        },
        "quality": {
            "min_g8_det_j": mesh.quality.min_det_j,
            "min_corner_volume": mesh.quality.min_corner_volume,
            "invalid_count": mesh.quality.invalid_count,
        },
        "curvature": asdict(mesh.curvature),
        "translated_feb": str(output_feb),
    }
    report_path = args.output_dir / "bottom-frame-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("[4/4] checking FEBio model initialization", flush=True)
    initialized = run_until_initialized(
        config.febio_exe,
        output_feb,
        args.output_dir / "febio-initialization-console.log",
    )
    report["febio_initialization_passed"] = initialized
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not initialized:
        print("FEBio initialization did not reach the first time step", file=sys.stderr)
        return 1
    print("BOTTOM_FRAME_INITIALIZATION_PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
