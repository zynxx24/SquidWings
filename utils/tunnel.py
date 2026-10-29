"""
utils.tunnel — Cloudflare Tunnel Manager.

Automatically manages a Cloudflare quick tunnel (trycloudflare.com)
to expose the local web GUI server to the internet without port forwarding.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

_GLOBAL_TUNNEL: CloudflareTunnel | None = None


def find_cloudflared_binary() -> Path | None:
    """Find cloudflared executable in PATH or standard user directories."""
    # 1. System PATH
    found = shutil.which("cloudflared")
    if found:
        return Path(found)

    # 2. Known standard paths
    candidates = [
        Path.home() / ".local" / "bin" / "cloudflared",
        Path("/usr/local/bin/cloudflared"),
        Path("/usr/bin/cloudflared"),
        Path.home() / "bin" / "cloudflared",
        Path.cwd() / "cloudflared",
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return c

    return None


def download_cloudflared() -> Path | None:
    """Attempt to download official cloudflared binary to ~/.local/bin/cloudflared."""
    import platform
    import urllib.request

    machine = platform.machine().lower()
    system = platform.system().lower()
    if system != "linux":
        logger.warning(f"[tunnel] Auto-download only supports Linux, current OS is {system}")
        return None

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif "arm" in machine:
        arch = "arm"
    else:
        logger.warning(f"[tunnel] Unsupported architecture: {machine}")
        return None

    target_dir = Path.home() / ".local" / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "cloudflared"

    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
    logger.info(f"[tunnel] Downloading cloudflared from {url}...")
    try:
        urllib.request.urlretrieve(url, target_file)
        target_file.chmod(0o755)
        logger.info(f"[tunnel] cloudflared downloaded successfully to {target_file}")
        return target_file
    except Exception as e:
        logger.error(f"[tunnel] Failed to download cloudflared: {e}")
        return None


class CloudflareTunnel:
    """Manages a quick Cloudflare Tunnel process."""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self.process: subprocess.Popen[str] | None = None
        self.public_url: str | None = None
        self._log_file: Path | None = None

    def start(self, timeout: float = 20.0) -> str | None:
        """Start the tunnel and wait up to timeout seconds for public URL."""
        global _GLOBAL_TUNNEL
        cf_bin = find_cloudflared_binary()
        if not cf_bin:
            logger.info("[tunnel] cloudflared not found, attempting auto-download...")
            cf_bin = download_cloudflared()

        if not cf_bin:
            logger.error("[tunnel] cloudflared binary could not be found or downloaded.")
            return None

        if self._log_file is None:
            self._log_file = Path.cwd() / "tunnel.log"
        target_url = f"http://{self.host}:{self.port}"

        cmd = [
            str(cf_bin),
            "tunnel",
            "--url",
            target_url,
            "--no-autoupdate",
        ]

        logger.info(f"[tunnel] Launching Cloudflare Tunnel to {target_url}...")
        try:
            with open(self._log_file, "w", encoding="utf-8") as log_f:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
        except Exception as e:
            logger.error(f"[tunnel] Failed to start cloudflared process: {e}")
            return None

        _GLOBAL_TUNNEL = self
        atexit.register(self.stop)

        # Poll for URL in log file
        start_t = time.time()
        url_regex = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        while time.time() - start_t < timeout:
            if self.process.poll() is not None:
                logger.error(f"[tunnel] cloudflared process exited unexpectedly with code {self.process.returncode}")
                break

            if self._log_file.exists():
                try:
                    content = self._log_file.read_text(encoding="utf-8", errors="ignore")
                    m = url_regex.search(content)
                    if m:
                        self.public_url = m.group(0)
                        logger.info(f"[tunnel] Cloudflare Tunnel online: {self.public_url}")
                        return self.public_url
                except Exception:
                    pass

            time.sleep(0.4)

        if not self.public_url:
            logger.warning("[tunnel] Tunnel started but public URL not detected within timeout. Check tunnel.log")

        return self.public_url

    def stop(self) -> None:
        """Stop the tunnel process cleanly."""
        global _GLOBAL_TUNNEL
        if self.process:
            logger.info("[tunnel] Stopping Cloudflare Tunnel...")
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            except Exception as e:
                logger.debug(f"[tunnel] Error stopping process: {e}")
            self.process = None

        if _GLOBAL_TUNNEL is self:
            _GLOBAL_TUNNEL = None


def get_current_tunnel() -> CloudflareTunnel | None:
    """Get the currently active Cloudflare tunnel instance."""
    return _GLOBAL_TUNNEL


def get_public_url() -> str | None:
    """Get the public URL of the active tunnel if available."""
    if _GLOBAL_TUNNEL:
        return _GLOBAL_TUNNEL.public_url
    return None
