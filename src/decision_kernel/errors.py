"""Decision-kernel error types."""

from __future__ import annotations


class DecisionKernelError(RuntimeError):
    """Base class for decision-kernel failures."""


class CertificateVerificationError(DecisionKernelError):
    """Raised when a certificate violates verifier law."""


class CertificateSemanticDriftError(DecisionKernelError):
    """Raised when a semantic certificate key recomputes to a different hash."""


class CompileFailureError(DecisionKernelError):
    """Raised when compiler output cannot satisfy a requested claim."""
