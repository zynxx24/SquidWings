"""
tool.sandbox.permission_guard — Permission checking for actions.

Blocks dangerous actions like writing outside the project directory,
deleting system files, or accessing non-whitelisted hosts.
"""

from __future__ import annotations

from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


class PermissionGuard:
    """Checks permissions for agent actions.

    Guards against:
    - Writing outside the project directory
    - Deleting system/protected files
    - Network access to non-whitelisted hosts
    """

    # Directories that are always protected
    PROTECTED_DIRS: list[str] = [
        "/bin", "/sbin", "/usr/bin", "/usr/sbin",
        "/etc", "/boot", "/dev", "/proc", "/sys",
        "/var/log", "/root",
    ]

    def __init__(
        self,
        project_root: str | Path | None = None,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        """Initialize the guard.

        Args:
            project_root: Root directory of the project. Actions outside
                          this directory are blocked.
            allowed_hosts: Whitelisted network hosts (e.g., ["localhost"]).
        """
        self._root = Path(project_root).resolve() if project_root else None
        self._allowed_hosts = set(allowed_hosts or ["localhost", "127.0.0.1"])

    def check_permission(self, action: str, target: str) -> bool:
        """Check if an action on a target is permitted.

        Args:
            action: Action type ("write", "delete", "read", "network").
            target: Target path or host.

        Returns:
            True if the action is permitted.
        """
        if action == "write":
            return self._check_write(target)
        elif action == "delete":
            return self._check_delete(target)
        elif action == "read":
            return self._check_read(target)
        elif action == "network":
            return self._check_network(target)
        else:
            logger.warning(f"[guard] Unknown action: {action}")
            return False

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        """Compare resolved path components, not similarly-prefixed strings."""
        return path == parent or parent in path.parents

    def _check_write(self, path: str) -> bool:
        """Check write permission."""
        resolved = Path(path).resolve()

        for protected in self.PROTECTED_DIRS:
            if self._is_within(resolved, Path(protected)):
                logger.warning(f"[guard] BLOCKED write to protected: {path}")
                return False

        if self._root and not self._is_within(resolved, self._root):
            logger.warning(f"[guard] BLOCKED write outside project: {path}")
            return False

        return True

    def _check_delete(self, path: str) -> bool:
        """Check delete permission — stricter than write."""
        resolved = Path(path).resolve()

        for protected in self.PROTECTED_DIRS:
            if self._is_within(resolved, Path(protected)):
                logger.warning(f"[guard] BLOCKED delete of protected: {path}")
                return False

        if self._root and not self._is_within(resolved, self._root):
            logger.warning(f"[guard] BLOCKED delete outside project: {path}")
            return False

        # Block deletion of project root itself
        if self._root and resolved == self._root:
            logger.warning(f"[guard] BLOCKED delete of project root: {path}")
            return False

        return True

    def _check_read(self, path: str) -> bool:
        """Check read permission — generally permissive."""
        resolved = Path(path).resolve()

        # Block reading sensitive system files
        sensitive = ["/etc/shadow", "/etc/passwd", "/etc/sudoers"]
        if str(resolved) in sensitive:
            logger.warning(f"[guard] BLOCKED read of sensitive: {path}")
            return False

        return True

    def _check_network(self, host: str) -> bool:
        """Check network access permission."""
        if host in self._allowed_hosts:
            return True
        logger.warning(f"[guard] BLOCKED network to: {host}")
        return False
