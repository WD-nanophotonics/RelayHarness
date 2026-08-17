"""RelayHarness: durable local coordination primitives for AI agents."""

__version__ = "0.1.0"

from .config import ProjectProfile
from .endpoints import AgentEndpoint, ExecutionIdentity
from .runtime import RuntimeLayout

__all__ = ["AgentEndpoint", "ExecutionIdentity", "ProjectProfile", "RuntimeLayout", "__version__"]
