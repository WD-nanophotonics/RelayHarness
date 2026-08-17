"""Hard model-selection boundary for future real-agent launches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .errors import ModelPolicyError


REQUIRED_MODEL = "Luna"
REQUIRED_REASONING = "High"


@dataclass(frozen=True)
class ModelRequest:
    model: str
    reasoning: str


@dataclass(frozen=True)
class ModelEvidence:
    model: str
    reasoning: str
    source: str


class ModelPolicy:
    """Fail-closed policy; defaults and aliases are deliberately not accepted."""

    @staticmethod
    def validate_request(request: ModelRequest) -> None:
        if request.model != REQUIRED_MODEL or request.reasoning != REQUIRED_REASONING:
            raise ModelPolicyError(
                f"explicit model request must be {REQUIRED_MODEL} {REQUIRED_REASONING}; "
                f"got {request.model!r} {request.reasoning!r}"
            )

    @staticmethod
    def validate_evidence(evidence: ModelEvidence | Mapping[str, str]) -> None:
        model = evidence.model if isinstance(evidence, ModelEvidence) else evidence.get("model")
        reasoning = evidence.reasoning if isinstance(evidence, ModelEvidence) else evidence.get("reasoning")
        if model != REQUIRED_MODEL or reasoning != REQUIRED_REASONING:
            raise ModelPolicyError(
                f"verified model evidence must be {REQUIRED_MODEL} {REQUIRED_REASONING}; "
                f"got {model!r} {reasoning!r}"
            )
