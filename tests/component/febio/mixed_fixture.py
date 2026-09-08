"""Registered synthetic reader-to-preview fixture for mixed consumer coverage."""

from pathlib import Path
from typing import Any

from febio_cae.adapters.preview import studio as preview
from febio_cae.domain import PreviewRequest, ToolIdentity

from .fixtures import evidence
from .reader_fixture import setup_reader


class MixedPreviewCase:
    def __init__(self, tmp_path: Path) -> None:
        reader, attempt, bundle, _, _ = setup_reader(tmp_path)
        self.manifest = reader.read(attempt, bundle)
        self.path = Path(attempt.process.cwd) / "output/results.xplt"
        self.studio = ToolIdentity("febio-studio", "2.8.0", "f" * 64)
        self.request = PreviewRequest(
            "mixed-preview", self.manifest.manifest_id, (0, 1), ("displacement",)
        )
        self.evidence = (evidence("preview.confirmation", "synthetic-independent-observation"),)
        self.launched: list[Path] = []
        self.observed = 0

    def launch(self, binding: preview.PreviewBinding) -> preview.PreviewLaunchResult:
        self.launched.append(binding.path)
        assert binding.studio == self.studio
        return preview.PreviewLaunchResult(binding, True)

    def observe(self, binding: preview.PreviewBinding) -> preview.PreviewObservation:
        self.observed += 1
        return preview.PreviewObservation((0, 1), ("displacement",), binding, self.evidence)

    def adapter(self, **overrides: Any) -> preview.PreviewAdapter:
        options: dict[str, Any] = {
            "studio": self.studio,
            "source": preview.FileSystemPreviewSource(self.path),
            "launcher": self.launch,
            "observer": self.observe,
        }
        options.update(overrides)
        return preview.PreviewAdapter(**options)
