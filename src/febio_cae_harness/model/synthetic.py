"""Runtime-only generation of a tiny, explicitly synthetic FEB input."""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from .feb import inspect_feb_file
from .preflight import run_preflight

SYNTHETIC_LABEL = "synthetic"
SYNTHETIC_UNITS = "mm-N-s"
SYNTHETIC_MATERIAL = "neo-Hookean"
SYNTHETIC_LOAD = 1.0
SYNTHETIC_CONSTRAINT = "fixed"
SYNTHETIC_EXPECTED_STEPS = 1
SYNTHETIC_FINAL_TIME = 1.0

_REPARSE_POINT = 0x400
_PAYLOAD = b"""<?xml version="1.0" encoding="UTF-8"?>
<!-- synthetic: units=mm-N-s; material=neo-Hookean; load=1 N; -->
<!-- synthetic: constraint=fixed; steps=1; final_time=1 s -->
<febio_spec version="4.0">
<Module type="solid"/><Control><analysis type="static"/>
<time_steps>1</time_steps><step_size>1</step_size></Control>
<Globals><Constants><T>0</T><R>0</R><Fc>0</Fc></Constants></Globals>
<Material><material id="1" name="synthetic_material" type="neo-Hookean">
<E>1000</E><v>0.3</v></material></Material>
<Mesh><Nodes name="synthetic_nodes"><node id="1">0,0,0</node><node id="2">1,0,0</node>
<node id="3">0,1,0</node><node id="4">0,0,1</node></Nodes>
<Elements type="tet4" name="synthetic_body" mat="1"><elem id="1">1,2,3,4</elem></Elements>
<NodeSet name="fixed">1,2,3</NodeSet><NodeSet name="loaded">4</NodeSet></Mesh>
<Step name="synthetic_step"><Control><time_steps>1</time_steps><step_size>1</step_size></Control>
<Boundary><fix bc="x,y,z" node_set="fixed"/></Boundary><Loads>
<nodal_load bc="z" node_set="loaded" type="dead">1</nodal_load></Loads></Step>
</febio_spec>
"""


@dataclass(frozen=True, slots=True)
class SyntheticFebReceipt:
    """Immutable receipt for one newly created synthetic FEB input."""

    destination: Path
    sha256: str
    expected_steps: int
    final_time: float
    synthetic: bool = True
    label: str = SYNTHETIC_LABEL
    units: str = SYNTHETIC_UNITS
    material: str = SYNTHETIC_MATERIAL
    load_value: float = SYNTHETIC_LOAD
    constraint: str = SYNTHETIC_CONSTRAINT
    result_claimed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "destination", Path(self.destination))
        if (self.synthetic, self.label, self.result_claimed) != (True, SYNTHETIC_LABEL, False):
            raise ValueError("receipt must be synthetic and make no result claims")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        if isinstance(self.expected_steps, bool) or self.expected_steps < 1:
            raise ValueError("expected_steps must be positive")
        if isinstance(self.final_time, bool) or not isfinite(self.final_time):
            raise ValueError("final_time must be finite")
        if self.final_time <= 0:
            raise ValueError("final_time must be positive")

    @property
    def kind(self) -> str:
        return self.label

    @property
    def path(self) -> Path:
        return self.destination

    @property
    def result_claims(self) -> tuple[str, ...]:
        return ()

    def to_dict(self) -> dict[str, object]:
        return {
            "destination": str(self.destination),
            "sha256": self.sha256,
            "expected_steps": self.expected_steps,
            "final_time": self.final_time,
            "synthetic": self.synthetic,
            "label": self.label,
            "units": self.units,
            "material": self.material,
            "load_value": self.load_value,
            "constraint": self.constraint,
            "result_claimed": self.result_claimed,
            "result_claims": list(self.result_claims),
        }


def _has_alias(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.lstat().st_file_attributes & _REPARSE_POINT)
    except (AttributeError, FileNotFoundError):
        return False


def _regular_single_link_identity(path: Path, metadata: os.stat_result) -> tuple[int, int]:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"synthetic destination is not a regular file: {path}")
    if metadata.st_nlink != 1:
        raise ValueError(f"synthetic destination must have exactly one link: {path}")
    return metadata.st_dev, metadata.st_ino


def _verify_target_identity(target: Path, created_identity: tuple[int, int]) -> None:
    observed = _regular_single_link_identity(target, target.lstat())
    if observed != created_identity:
        raise ValueError(f"synthetic destination identity changed: {target}")


def _cleanup_created_target(target: Path, created_identity: tuple[int, int] | None) -> None:
    if created_identity is None:
        return
    try:
        metadata = target.lstat()
    except OSError:
        return
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        return
    if (metadata.st_dev, metadata.st_ino) != created_identity:
        return
    with suppress(OSError):
        target.unlink()


def _reject_alias_components(path: Path) -> None:
    current = path
    while True:
        if _has_alias(current):
            raise ValueError(f"path alias/reparse point is not allowed: {path}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _absolute_path(value: str | os.PathLike[str]) -> Path:
    path = Path(os.fspath(value))
    return path if path.is_absolute() else Path.cwd() / path


def _destination_path(
    attempt: Path,
    destination: str | os.PathLike[str],
) -> Path:
    candidate = Path(os.fspath(destination))
    if candidate.is_absolute():
        target = candidate
    else:
        if candidate.parent != Path("."):
            raise ValueError("destination filename must be a single .feb filename")
        target = attempt / candidate
    if target.suffix.lower() != ".feb" or not target.name:
        raise ValueError("destination filename must be a single .feb filename")
    _reject_alias_components(target)
    if target.parent != attempt:
        raise ValueError("destination must remain inside the attempt directory")
    return target


def generate_synthetic_feb(
    attempt_dir: str | os.PathLike[str],
    destination: str | os.PathLike[str] = "synthetic.feb",
    *,
    destination_name: str | os.PathLike[str] | None = None,
) -> SyntheticFebReceipt:
    """Create one synthetic FEB input in an existing empty attempt directory.

    The destination is opened with exclusive-create semantics.  No solver,
    FBS adapter, result file, or physical interpretation is involved.
    """

    if destination_name is not None:
        if destination != "synthetic.feb":
            raise TypeError("provide destination or destination_name, not both")
        destination = destination_name
    attempt = _absolute_path(attempt_dir)
    _reject_alias_components(attempt)
    if not attempt.exists():
        raise FileNotFoundError(f"attempt directory does not exist: {attempt}")
    if not attempt.is_dir():
        raise NotADirectoryError(f"attempt path is not a directory: {attempt}")
    attempt = attempt.resolve(strict=True)
    target = _destination_path(attempt, destination)
    if os.path.lexists(os.fspath(target)):
        raise FileExistsError(target)
    if any(attempt.iterdir()):
        raise ValueError("attempt directory must be empty")

    created_identity: tuple[int, int] | None = None
    open_fd: int | None = None
    try:
        opened_fd = os.open(
            os.fspath(target),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o600,
        )
        open_fd = opened_fd
        created_identity = _regular_single_link_identity(target, os.fstat(opened_fd))
        with os.fdopen(opened_fd, "wb") as stream:
            open_fd = None
            stream.write(_PAYLOAD)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_target_identity(target, created_identity)
        inspection = inspect_feb_file(target)
        preflight = run_preflight(feb=inspection)
        if not preflight.ready:
            details = "; ".join(item.message for item in preflight.diagnostics)
            raise ValueError(f"generated synthetic FEB failed preflight: {details}")
        _verify_target_identity(target, created_identity)
    except BaseException:
        if open_fd is not None:
            with suppress(OSError):
                os.close(open_fd)
        _cleanup_created_target(target, created_identity)
        raise

    return SyntheticFebReceipt(
        destination=target,
        sha256=hashlib.sha256(_PAYLOAD).hexdigest(),
        expected_steps=SYNTHETIC_EXPECTED_STEPS,
        final_time=SYNTHETIC_FINAL_TIME,
    )


write_synthetic_feb = create_synthetic_feb = generate_synthetic_feb
SyntheticFEBReceipt = SyntheticFebReceipt

__all__ = [
    "SYNTHETIC_CONSTRAINT",
    "SYNTHETIC_EXPECTED_STEPS",
    "SYNTHETIC_FINAL_TIME",
    "SYNTHETIC_LABEL",
    "SYNTHETIC_LOAD",
    "SYNTHETIC_MATERIAL",
    "SYNTHETIC_UNITS",
    "SyntheticFEBReceipt",
    "SyntheticFebReceipt",
    "create_synthetic_feb",
    "generate_synthetic_feb",
    "write_synthetic_feb",
]
