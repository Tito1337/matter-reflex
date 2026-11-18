"""Verify the precise public payload for Matter Reflex diagnostics."""

import json
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.matter_reflex.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.matter_reflex.runtime import Runtime


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "matches", "acl_synced", "expected_sync"),
    [
        ("matter", True, True, True),
        ("http", False, True, False),
        ("matter", True, False, False),
    ],
)
async def test_diagnostics_allowlist(transport, matches, acl_synced, expected_sync):
    runtime = Runtime.__new__(Runtime)
    runtime.channel = object() if transport == "http" else None
    runtime.status = {
        "hash": "unpublished SHA",
        "active": True,
        "executions": 0,
        "node_id": "private ID",
        "address": "http://private-address",
    }
    runtime.target_hash = runtime.status["hash"] if matches else "another SHA"
    runtime.acl_synced = acl_synced
    runtime.acls = object()
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        runtime_data=runtime,
        title="private name",
        data={"key": "private key", "dataset": "private dataset"},
    )

    payload = await async_get_config_entry_diagnostics(None, entry)

    assert json.loads(json.dumps(payload)) == {
        "rules_transport": transport,
        "status_available": True,
        "reflex_active": True,
        "rules_synchronized": expected_sync,
        "reported_rule_results_since_boot": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded", [True, False])
async def test_diagnostics_when_status_is_unavailable(loaded):
    runtime = Runtime.__new__(Runtime)
    runtime.channel = None
    runtime.status = None if loaded else {"private": "stale status"}
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED,
        runtime_data=runtime,
    )

    assert await async_get_config_entry_diagnostics(None, entry) == {
        "rules_transport": "matter" if loaded else None,
        "status_available": False,
        "reflex_active": None,
        "rules_synchronized": None,
        "reported_rule_results_since_boot": None,
    }
