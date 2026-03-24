"""Unit tests for the MCP server module (pineapple.mcp_server).

Tests basic server construction and tool registration.
Gracefully skips if fastmcp is not installed.
"""
import pytest

fastmcp = pytest.importorskip("fastmcp", reason="fastmcp not installed")


class TestMCPServerCreation:
    """Tests that the MCP server can be created and tools are registered."""

    def test_mcp_server_exists(self):
        """The mcp server object should be importable."""
        from pineapple.mcp_server import mcp
        assert mcp is not None
        assert mcp.name == "pineapple-pipeline"

    def test_pineapple_run_tool_registered(self):
        """pineapple_run should be a callable tool function."""
        from pineapple.mcp_server import pineapple_run
        assert callable(pineapple_run)

    def test_pineapple_status_tool_registered(self):
        """pineapple_status should be a callable tool function."""
        from pineapple.mcp_server import pineapple_status
        assert callable(pineapple_status)

    def test_pineapple_approve_tool_registered(self):
        """pineapple_approve should be a callable tool function."""
        from pineapple.mcp_server import pineapple_approve
        assert callable(pineapple_approve)

    def test_pineapple_get_state_tool_registered(self):
        """pineapple_get_state should be a callable tool function."""
        from pineapple.mcp_server import pineapple_get_state
        assert callable(pineapple_get_state)


class TestFlushHelper:
    """Tests for the _flush helper."""

    def test_flush_does_not_raise(self):
        """_flush should never raise, even without LangFuse."""
        from pineapple.mcp_server import _flush
        # Should complete without error
        _flush()
