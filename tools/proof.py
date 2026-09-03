from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from ..schemas import (
    Atom,
    EvidenceItem,
    EvidenceType,
    Label,
    ProofStep,
    Question,
    VerificationResult,
    World,
)


VARIABLES = frozenset({"someone", "something"})


def is_variable(term: str) -> bool:
    return term.lower() in VARIABLES


def _unify_term(pattern: str, value: str, substitution: dict[str, str]) -> bool:
    if is_variable(pattern):
        bound = substitution.get(pattern)
        if bound is None:
            substitution[pattern] = value
            return True
        return bound == value
    return pattern == value


def unify(pattern: Atom, ground: Atom, substitution: Optional[dict[str, str]] = None) -> dict[str, str] | None:
    if pattern.polarity != ground.polarity:
        return None
    result = dict(substitution or {})
    for pattern_term, ground_term in (
        (pattern.subject, ground.subject),
        (pattern.relation, ground.relation),
        (pattern.object, ground.object),
    ):
        if not _unify_term(pattern_term, ground_term, result):
            return None
    return result


def ground_atom(pattern: Atom, substitution: Mapping[str, str]) -> Atom | None:
    terms = []
    for term in (pattern.subject, pattern.relation, pattern.object):
        grounded = substitution.get(term, term) if is_variable(term) else term
        if is_variable(grounded):
            return None
        terms.append(grounded)
    return Atom(terms[0], terms[1], terms[2], pattern.polarity)


@dataclass(frozen=True)
class Derivation:
    atom: Atom
    evidence_id: str
    premise_keys: tuple[str, ...]
    depth: int


@dataclass
class ClosureResult:
    derivations: dict[str, Derivation]
    iterations: int

    def contains(self, atom: Atom) -> bool:
        return atom.key in self.derivations


class ForwardChainer:
    def derive(self, evidence: Iterable[EvidenceItem], max_iterations: int = 100) -> ClosureResult:
        items = list(evidence)
        derivations: dict[str, Derivation] = {}
        rules = []

        for item in items:
            if item.evidence_type == EvidenceType.FACT:
                assert item.atom is not None
                derivations.setdefault(
                    item.atom.key,
                    Derivation(item.atom, item.evidence_id, (), 0),
                )
            else:
                assert item.rule is not None
                rules.append(item.rule)

        iterations = 0
        changed = True
        while changed and iterations < max_iterations:
            iterations += 1
            changed = False
            known = [x.atom for x in derivations.values()]

            for rule in rules:
                matches: list[tuple[dict[str, str], tuple[str, ...]]] = [({}, ())]
                for premise_pattern in rule.body:
                    next_matches: list[tuple[dict[str, str], tuple[str, ...]]] = []
                    for substitution, premise_keys in matches:
                        for candidate in known:
                            unified = unify(premise_pattern, candidate, substitution)
                            if unified is not None:
                                next_matches.append((unified, premise_keys + (candidate.key,)))
                    matches = next_matches
                    if not matches:
                        break

                for substitution, premise_keys in matches:
                    conclusion = ground_atom(rule.head, substitution)
                    if conclusion is None or conclusion.key in derivations:
                        continue
                    premise_depths = [derivations[key].depth for key in premise_keys]
                    derivations[conclusion.key] = Derivation(
                        atom=conclusion,
                        evidence_id=rule.rule_id,
                        premise_keys=premise_keys,
                        depth=(max(premise_depths) if premise_depths else 0) + 1,
                    )
                    changed = True

        return ClosureResult(derivations=derivations, iterations=iterations)

    def extract_proof(self, closure: ClosureResult, target: Atom) -> list[ProofStep]:
        if target.key not in closure.derivations:
            return []

        steps: list[ProofStep] = []
        step_by_atom: dict[str, str] = {}

        def visit(atom_key: str) -> str:
            if atom_key in step_by_atom:
                return step_by_atom[atom_key]
            derivation = closure.derivations[atom_key]
            premise_step_ids = tuple(visit(key) for key in derivation.premise_keys)
            step_id = f"p{len(steps) + 1}"
            steps.append(
                ProofStep(
                    step_id=step_id,
                    conclusion=derivation.atom,
                    evidence_id=derivation.evidence_id,
                    premise_step_ids=premise_step_ids,
                )
            )
            step_by_atom[atom_key] = step_id
            return step_id

        visit(target.key)
        return steps


class ProofVerifier:
    def __init__(self, chainer: ForwardChainer | None = None) -> None:
        self.chainer = chainer or ForwardChainer()

    @staticmethod
    def required_signatures(world: World, question: Question) -> set[tuple[str, str]]:
        required = {question.atom.signature, question.atom.complement().signature}
        changed = True
        while changed:
            changed = False
            for item in world.rules:
                assert item.rule is not None
                if item.rule.head.signature not in required:
                    continue
                for premise in item.rule.body:
                    if premise.signature not in required:
                        required.add(premise.signature)
                        changed = True
        return required

    def required_evidence_ids(self, world: World, question: Question) -> set[str]:
        signatures = self.required_signatures(world, question)
        return {
            item.evidence_id
            for item in world.evidence.values()
            if item.signature in signatures
        }

    def coverage(
        self,
        world: World,
        question: Question,
        retrieved_ids: set[str],
        completed_request_keys: set[str] | None = None,
    ) -> tuple[bool, list[tuple[str, str]]]:
        required_signatures = self.required_signatures(world, question)
        completed_request_keys = completed_request_keys or set()
        covered_signatures = {
            (predicate, polarity)
            for predicate, polarity in required_signatures
            if {
                f"fact:{predicate}:{polarity}",
                f"rule:{predicate}:{polarity}",
            }.issubset(completed_request_keys)
        }
        missing = sorted(required_signatures - covered_signatures)

        required_ids = self.required_evidence_ids(world, question)
        if not required_ids.issubset(retrieved_ids):
            missing_id_signatures = {
                world.evidence[evidence_id].signature
                for evidence_id in required_ids - retrieved_ids
            }
            missing = sorted(set(missing) | missing_id_signatures)
        return (not missing and required_ids.issubset(retrieved_ids), missing)

    def infer_label(self, evidence: Iterable[EvidenceItem], question: Question) -> VerificationResult:
        closure = self.chainer.derive(evidence)
        target = question.atom
        opposite = target.complement()
        has_target = closure.contains(target)
        has_opposite = closure.contains(opposite)

        if has_target and has_opposite:
            return VerificationResult(False, Label.INVALID, "both target and complement are derivable")
        if has_target:
            return VerificationResult(
                True,
                Label.TRUE,
                "target is derivable",
                proof=self.chainer.extract_proof(closure, target),
            )
        if has_opposite:
            return VerificationResult(
                True,
                Label.FALSE,
                "explicit complement is derivable",
                proof=self.chainer.extract_proof(closure, opposite),
            )
        return VerificationResult(True, Label.UNKNOWN, "neither target nor complement is derivable")

    def verify_claim(
        self,
        world: World,
        question: Question,
        retrieved_ids: set[str],
        claimed_label: Label,
    ) -> VerificationResult:
        selected = [world.evidence[x] for x in retrieved_ids if x in world.evidence]
        inferred = self.infer_label(selected, question)
        if inferred.label != claimed_label:
            return VerificationResult(
                False,
                claimed_label,
                f"claim {claimed_label.value} disagrees with retrieved evidence ({inferred.label.value})",
                proof=inferred.proof,
            )

        if claimed_label == Label.UNKNOWN:
            complete, missing = self.coverage(world, question, retrieved_ids)
            if not complete:
                return VerificationResult(
                    False,
                    Label.UNKNOWN,
                    "unknown claim lacks a complete coverage certificate",
                    coverage_complete=False,
                    missing_signatures=missing,
                )
            inferred.coverage_complete = True
        return inferred

    def validate_proof_steps(
        self,
        world: World,
        proof: list[ProofStep],
        expected: Atom,
    ) -> tuple[bool, str]:
        by_step: dict[str, Atom] = {}
        for step in proof:
            item = world.evidence.get(step.evidence_id)
            if item is None:
                return False, f"unknown evidence id: {step.evidence_id}"
            if item.evidence_type == EvidenceType.FACT:
                if step.premise_step_ids:
                    return False, f"fact step {step.step_id} has premises"
                if item.atom != step.conclusion:
                    return False, f"fact step {step.step_id} conclusion mismatch"
            else:
                assert item.rule is not None
                if len(step.premise_step_ids) != len(item.rule.body):
                    return False, f"rule step {step.step_id} premise count mismatch"
                substitution: dict[str, str] = {}
                for pattern, premise_step_id in zip(item.rule.body, step.premise_step_ids):
                    if premise_step_id not in by_step:
                        return False, f"step {step.step_id} references unavailable premise"
                    unified = unify(pattern, by_step[premise_step_id], substitution)
                    if unified is None:
                        return False, f"step {step.step_id} has invalid premise binding"
                    substitution = unified
                grounded = ground_atom(item.rule.head, substitution)
                if grounded != step.conclusion:
                    return False, f"rule step {step.step_id} conclusion mismatch"
            by_step[step.step_id] = step.conclusion

        if not proof or proof[-1].conclusion != expected:
            return False, "proof does not end at expected conclusion"
        return True, "proof is valid"
