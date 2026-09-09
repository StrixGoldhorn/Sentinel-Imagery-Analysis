"""Bootstrap re-export of cooperative shutdown coordinator and signal management."""

from sentinel_analysis.application.shutdown import ShutdownCoordinator, shutdown_coordinator

__all__ = ["ShutdownCoordinator", "shutdown_coordinator"]
