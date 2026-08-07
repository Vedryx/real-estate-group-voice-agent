"""Tests for the dummy-data production gate (E3) and lead-writer validation (E5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent import data_store as ds
from agent.canopy import CanopyKnowledge


# ------------------------------------------------------------------ E3 gate
def test_dummy_commercial_blocks_production(monkeypatch):
    from agent.worker import _guard_commercial_data

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        _guard_commercial_data(CanopyKnowledge.load())  # commercial is DUMMY


def test_dummy_commercial_allows_non_production(monkeypatch):
    from agent.worker import _guard_commercial_data

    monkeypatch.setenv("ENVIRONMENT", "development")
    _guard_commercial_data(CanopyKnowledge.load())  # warns, does not raise


# ------------------------------------------------------------------ E5 validation
def test_writer_rejects_non_serializable_value(tmp_path):
    with pytest.raises(TypeError):
        ds.log_lead(
            caller_phone=MagicMock(),  # would have persisted "<MagicMock ...>" before
            outcome="callback_requested",
            log_path=tmp_path / "leads.jsonl",
        )
    # nothing written on rejection
    assert not (tmp_path / "leads.jsonl").exists()


def test_writer_accepts_valid_record(tmp_path):
    path = tmp_path / "leads.jsonl"
    rec = ds.log_lead(caller_phone="+919812345678", outcome="callback_requested", name="Asha", log_path=path)
    assert rec["outcome"] == "callback_requested"
    assert path.read_text().strip()
