from __future__ import annotations

import hashlib
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from febio_cae_harness.model import (
    SyntheticFebReceipt,
    generate_synthetic_feb,
    inspect_feb_file,
    run_preflight,
)


def test_generator_writes_a_closed_structural_input_and_immutable_receipt(
    tmp_path: Path,
) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir()

    receipt = generate_synthetic_feb(attempt)

    assert isinstance(receipt, SyntheticFebReceipt)
    assert receipt.synthetic is True
    assert receipt.label == "synthetic"
    assert receipt.kind == "synthetic"
    assert receipt.destination == attempt / "synthetic.feb"
    payload = receipt.destination.read_bytes()
    assert receipt.sha256 == hashlib.sha256(payload).hexdigest()
    assert receipt.expected_steps == 1
    assert receipt.final_time == 1.0
    assert receipt.units == "mm-N-s"
    assert receipt.material == "neo-Hookean"
    assert receipt.load_value == 1.0
    assert receipt.constraint == "fixed"
    assert receipt.result_claimed is False
    assert list(attempt.iterdir()) == [receipt.destination]

    assert b"mm-N-s" in payload
    assert b'type="neo-Hookean"' in payload
    assert b"<E>1000</E>" in payload
    assert b'<fix bc="x,y,z" node_set="fixed"' in payload
    assert b'<nodal_load bc="z" node_set="loaded" type="dead">1' in payload
    assert b"<time_steps>1</time_steps>" in payload
    assert b"final_time=1 s" in payload
    assert b"<Result" not in payload

    inspection = inspect_feb_file(receipt.destination)
    assert inspection.root_tag == "febio_spec"
    assert inspection.root_attributes["version"] == "4.0"
    assert inspection.tag_counts["node"] == 4
    assert inspection.tag_counts["elem"] == 1
    assert inspection.tag_counts["material"] == 1
    assert inspection.tag_counts["Step"] == 1
    assert inspection.references
    assert inspection.unresolved_references == ()
    assert inspection.duplicate_keys == ()
    assert run_preflight(feb=inspection).ready is True

    record = receipt.to_dict()
    assert record["synthetic"] is True
    assert record["result_claimed"] is False
    assert record["sha256"] == receipt.sha256
    with pytest.raises(FrozenInstanceError):
        receipt.sha256 = "tampered"  # type: ignore[misc]


def test_generator_is_create_new_and_requires_an_empty_attempt(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    destination = attempt / "synthetic.feb"
    destination.write_bytes(b"sentinel")

    with pytest.raises(FileExistsError):
        generate_synthetic_feb(attempt)
    assert destination.read_bytes() == b"sentinel"

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "marker.txt").write_text("sentinel", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        generate_synthetic_feb(nonempty)


def test_generator_rejects_unsafe_names_aliases_and_hardlinks(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir()

    with pytest.raises(ValueError, match="filename"):
        generate_synthetic_feb(attempt, destination_name="../escape.feb")
    with pytest.raises(ValueError, match=r"\.feb"):
        generate_synthetic_feb(attempt, destination_name="synthetic.xml")

    alias = tmp_path / "alias"
    try:
        alias.symlink_to(attempt, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(ValueError, match="alias"):
            generate_synthetic_feb(alias)

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"sentinel")
    hardlink = attempt / "synthetic.feb"
    try:
        os.link(outside, hardlink)
    except OSError:
        pytest.skip("hard links are unavailable in this environment")
    with pytest.raises(FileExistsError):
        generate_synthetic_feb(attempt)
    assert outside.read_bytes() == b"sentinel"
