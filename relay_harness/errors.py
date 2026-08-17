"""Errors raised by deterministic RelayHarness mechanisms."""


class RelayHarnessError(Exception):
    """Base class for expected operational errors."""


class SchemaError(RelayHarnessError, ValueError):
    """A durable object does not satisfy its schema."""


class IntegrityError(RelayHarnessError):
    """A hash, claim, or ownership invariant was violated."""


class ModelPolicyError(RelayHarnessError):
    """A launch or model evidence record is not Luna High."""


class RecoveryError(RelayHarnessError):
    """Durable state cannot be reconstructed safely."""


class BackendUnavailable(RelayHarnessError):
    """A requested endpoint backend is not available through the current host adapter."""
