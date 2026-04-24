"""LLM agents for fraud investigation routing, planning, and verification."""

from agents.composer import compose_projection_proposal
from agents.interpreter import interpret_structural_claim
from agents.planner import DEFAULT_BRANCH_SPEC, plan_branches
from agents.router import classify_hypothesis
from agents.stubs import build_structural_claim, choose_verifier_request
from agents.synthesizer import synthesize_verdict
from agents.verifier import select_verifier_request

__all__ = [
    "classify_hypothesis",
    "plan_branches",
    "DEFAULT_BRANCH_SPEC",
    "compose_projection_proposal",
    "interpret_structural_claim",
    "select_verifier_request",
    "synthesize_verdict",
    "build_structural_claim",
    "choose_verifier_request",
]
