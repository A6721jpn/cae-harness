"""Reuse this repository's registered synthetic case fixture."""

import importlib

_fixture = importlib.import_module("tests.component.application.test_persistence_authority")
_created = _fixture._created
_evidence = _fixture._evidence
_quality_registration = _fixture._quality_registration
complete_spec = _fixture.complete_spec
