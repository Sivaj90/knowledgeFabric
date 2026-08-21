"""Phase 2.5: query planner (HLD 8.1 step 0) -- a separate, single-purpose
LLM call, distinct from answer generation and the sufficiency check (design
doc section 6). Decides which engine(s) to invoke and whether the query
needs reframing. Graph is not a real routing option yet (no Apache AGE
data exists -- see design doc section 1's explicitly-out-of-scope list),
so the effective choices for Slice 2 are vector / keyword / hybrid.
"""

import json
from dataclasses import dataclass, field

from openai import OpenAI

from kb_fabric.config import get_settings

VALID_ENGINES = {"vector", "keyword", "hybrid"}


@dataclass
class QueryPlannerInput:
    """Explicit typed input -- design doc section 6.1. Only what the
    planner needs: the query and (optionally) prior turns for pronoun/
    context resolution. No retrieved chunks (nothing retrieved yet)."""

    query: str
    conversation_history: list[str] = field(default_factory=list)


@dataclass
class QueryPlannerOutput:
    engines: list[str]  # subset of {"vector", "keyword"} -- "hybrid" expands to both
    reframed_query: str | None  # None if no reframing was needed
    reasoning: str = ""


_SYSTEM_PROMPT = """You are a query-routing planner for a hybrid search system.
Given a user's question, decide:
1. Which search engine(s) should run: "vector" (semantic/paraphrase-heavy
   questions), "keyword" (exact terms, IDs, error codes, proper nouns), or
   "hybrid" (both -- the safe default when unsure).
2. Whether the query needs reframing before search -- expanding
   abbreviations, resolving pronouns using conversation history, or
   rewriting a vague query to better match how content is phrased. Return
   null for reframed_query if no reframing is needed.

You do not have access to any retrieved content yet -- reason only about
the query itself and any conversation history provided.

Respond with ONLY a JSON object, no other text:
{"engines": ["vector"|"keyword"|... ], "reframed_query": "..." or null, "reasoning": "one short sentence"}
"""


def plan_query(input_data: QueryPlannerInput) -> QueryPlannerOutput:
    """Single-purpose LLM call: routing + reframing decision only -- no
    answer generation, no sufficiency judgment (design doc section 6)."""
    settings = get_settings()
    client = OpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)

    history_block = ""
    if input_data.conversation_history:
        history_block = "\n\nConversation history (most recent last):\n" + "\n".join(
            input_data.conversation_history
        )

    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Query: {input_data.query}{history_block}"},
        ],
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or "{}"
    parsed = json.loads(raw)

    engines = parsed.get("engines", ["vector", "keyword"])
    if "hybrid" in engines:
        engines = ["vector", "keyword"]
    engines = [e for e in engines if e in VALID_ENGINES - {"hybrid"}] or ["vector", "keyword"]

    return QueryPlannerOutput(
        engines=engines,
        reframed_query=parsed.get("reframed_query") or None,
        reasoning=parsed.get("reasoning", ""),
    )
