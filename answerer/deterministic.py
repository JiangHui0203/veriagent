from __future__ import annotations

from ..schemas import CandidateProposal
from ..state.models import AgentState
from ..tools import ProofVerifier


class DeterministicAnswerer:
    """Produces a candidate from currently retrieved evidence only."""

    def __init__(self, verifier: ProofVerifier | None = None) -> None:
        self.verifier = verifier or ProofVerifier()

    def propose(self, state: AgentState) -> CandidateProposal:
        inferred = self.verifier.infer_label(
            state.evidence_ledger.values(),
            state.task.question,
        )
        return CandidateProposal(
            answer=inferred.label,
            reasoning=inferred.reason,
            proof=inferred.proof,
            evidence_ids={step.evidence_id for step in inferred.proof},
        )
