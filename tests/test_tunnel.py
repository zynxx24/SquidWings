"""
tests.test_tunnel — Unit tests for Cloudflare Tunnel utility.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.tunnel import (
    CloudflareTunnel,
    find_cloudflared_binary,
    get_current_tunnel,
    get_public_url,
)


def test_find_cloudflared_binary():
    """Verify cloudflared binary finder finds the existing executable."""
    bin_path = find_cloudflared_binary()
    assert bin_path is not None
    assert bin_path.exists()


def test_tunnel_initial_state():
    """Verify initial tunnel helper state."""
    assert get_public_url() is None
    tunnel = CloudflareTunnel(port=8888, host="127.0.0.1")
    assert tunnel.port == 8888
    assert tunnel.host == "127.0.0.1"
    assert tunnel.public_url is None
    assert tunnel.process is None


def test_tunnel_start_and_stop_mocked(tmp_path):
    """Test start and stop lifecycle with mocked subprocess."""
    tunnel = CloudflareTunnel(port=8888)
    tunnel._log_file = tmp_path / "tunnel.log"
    tunnel._log_file.write_text(
        "2026-10-22 INF https://test-sample-tunnel.trycloudflare.com\n",
        encoding="utf-8",
    )

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    def mock_popen(*args, **kwargs):
        tunnel._log_file.write_text(
            "2026-10-22 INF https://test-sample-tunnel.trycloudflare.com\n",
            encoding="utf-8",
        )
        return mock_proc

    with patch("subprocess.Popen", side_effect=mock_popen):
        url = tunnel.start(timeout=1.0)
        assert url == "https://test-sample-tunnel.trycloudflare.com"
        assert get_current_tunnel() is tunnel
        assert get_public_url() == "https://test-sample-tunnel.trycloudflare.com"

        tunnel.stop()
        mock_proc.terminate.assert_called_once()
        assert get_current_tunnel() is None
        assert get_public_url() is None
