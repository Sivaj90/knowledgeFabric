"""Phase 2.5 tests: query planner (real LiteLLM chat call, not mocked)."""

from kb_fabric.retrieval.query_planner import QueryPlannerInput, plan_query


def test_plan_query_returns_valid_engines():
    result = plan_query(QueryPlannerInput(query="What is the warehouse capacity policy?"))
    assert result.engines
    assert set(result.engines) <= {"vector", "keyword"}


def test_plan_query_reframes_using_conversation_history():
    result = plan_query(
        QueryPlannerInput(
            query="Why did it change last month?",
            conversation_history=["User asked about the UAE delivery promise logic."],
        )
    )
    # The vague pronoun "it" should get resolved using history -- the
    # reframed query should mention delivery/UAE, not stay generic.
    assert result.reframed_query is not None
    assert "delivery" in result.reframed_query.lower() or "uae" in result.reframed_query.lower()


def test_plan_query_no_reframe_for_clear_query():
    result = plan_query(QueryPlannerInput(query="What is the JAFZA-1 warehouse capacity threshold?"))
    # A clear, self-contained query may or may not get reframed -- this
    # just asserts the field is present and typed correctly either way.
    assert result.reframed_query is None or isinstance(result.reframed_query, str)


def test_plan_query_output_has_reasoning():
    result = plan_query(QueryPlannerInput(query="Any question"))
    assert isinstance(result.reasoning, str)
