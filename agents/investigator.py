"""Variance Investigator Agent.

Per GROUP of similar variances: code builds an evidence pack with read-only tools, one LLM call
explains it (cheap model for simple groups, strong model for multi-cause), and a deterministic
groundedness check decides whether the explanation can be shown as a possible cause or must go
to a human. The agent never changes money or status beyond 'possible cause'.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents import groundedness
from agents.evidence import VarianceGroup, build_pack, group_variances
from llm.client import LLMClient
from llm.prompts import load_prompt
from llm.provider import LLMError
from privacy.scanner import PrivacyViolation
from schemas.enums import VarianceStatus
from schemas.variance import Finding, Variance

AGENT = "investigator"


@dataclass
class GroupOutcome:
    group: VarianceGroup
    task: str
    finding: Finding | None
    grounded: bool
    problems: list[str] = field(default_factory=list)
    cache_hit: bool = False
    error: str | None = None
    prompt_hash: str | None = None    # links the group to its exact cached LLM request/response


def investigate(conn, client: LLMClient, variances: list[Variance],
                only_complex: bool = False) -> tuple[list[Variance], list[GroupOutcome]]:
    prompt = load_prompt("investigate")
    groups = group_variances(variances)
    updated: list[Variance] = []
    outcomes: list[GroupOutcome] = []
    for g in groups:
        if only_complex and not g.is_complex:
            updated += g.variances
            continue
        task = "investigate_complex" if g.is_complex else "investigate_simple"
        pack = build_pack(conn, g)
        try:
            res = client.call(agent=AGENT, task=task, prompt=prompt, payload=pack, output_model=Finding)
        except (LLMError, PrivacyViolation) as exc:
            outcomes.append(GroupOutcome(g, task, None, False, error=f"{type(exc).__name__}: {exc}"))
            updated += [v.model_copy(update={"group_id": g.group_id, "status": VarianceStatus.NEEDS_HUMAN})
                        for v in g.variances]
            continue
        check = groundedness.check(res.output, pack)
        outcomes.append(GroupOutcome(g, task, res.output, check.grounded, check.problems, res.cache_hit,
                                     prompt_hash=res.prompt_hash))
        for v in g.variances:
            updated.append(v.model_copy(update={
                "group_id": g.group_id,
                "possible_cause_llm": res.output.possible_causes,
                "llm_confidence": res.output.confidence,
                "explanation": res.output.explanation,
                "evidence_refs": res.output.evidence_refs if v is g.representative else v.evidence_refs,
                "recommended_action": res.output.recommended_action,
                "grounded": check.grounded,
                # Grounded -> a possible cause for a human to confirm. Ungrounded -> human must classify.
                "status": VarianceStatus.POSSIBLE_CAUSE if check.grounded else VarianceStatus.NEEDS_HUMAN,
            }))
    return updated, outcomes
