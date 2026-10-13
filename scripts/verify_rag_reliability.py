"""Offline regression checks for evidence budgets, answer repair and quota waits."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import sys

import httpx
from openai import APIConnectionError, RateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.chat import ChatService, REFUSAL, _is_refusal
from app.citations import audit_citations, format_context, has_uncited_prose
from app.llm import LLMClient, rate_limit_delay
from app.retrieval import select_context
from eval.run_ragas import judge_faithfulness


def chunk(text, page="1", source="source"):
    return NS(source_id=source, locator_kind="page", locator_value=page,
              heading="", text=text, distance=.2, start_ts=None, end_ts=None)


def quota(message="Please retry in 39.5s.", **headers):
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.test/v1"), headers=headers)
    return RateLimitError(message, response=response, body={"message": message})


def main():
    assert _is_refusal("Based on the provided context, there is no mention of NASA Table 4.2-2 [1].")
    assert _is_refusal("Based on the provided context [1], aluminium is not listed. Steel costs 120.")
    assert _is_refusal("Based on the provided operating costs data [1], there is no mention of the cost for aluminium.")
    assert not _is_refusal("Based on the provided context, matrix multiplication is not commutative [1].")
    assert not _is_refusal("Based on the provided text, the framework does not prescribe risk tolerance [1].")
    print("PASS: source-absence answers normalize; supported negative facts remain answers")
    chunks = [chunk("Valid and reliable systems. " * 15), chunk("Other evidence. " * 40, "2")]
    budget = len(format_context(chunks[:1], numbered=True))
    selected = select_context(chunks, "valid reliable", limit=6, budget=budget)
    assert len(selected) == 1 and selected[0].text == chunks[0].text
    assert len(format_context(selected, numbered=True)) <= budget
    assert select_context(chunks, "valid", limit=6, budget=1) == []
    assert len(select_context([chunks[0], chunks[0]], "valid", limit=6, budget=6000)) == 1
    other_source = chunk(chunks[0].text, source="other")
    assert len(select_context([chunks[0], other_source], "valid", limit=6, budget=6000)) == 2
    broad = [chunk(f"Risk and trustworthiness concepts. General discussion {i}.", str(i // 3)) for i in range(15)]
    precise = chunk("Risks or trustworthiness characteristics which cannot be measured must be documented.", "34")
    result = select_context([*broad, precise], "What happens to risks or trustworthiness characteristics that cannot be measured?", limit=6, budget=6000)
    assert precise in result
    print("PASS: whole-chunk budget, deduplication and source identity")
    grouped = audit_citations("Facts [2, 1, 99].", chunks)
    assert grouped.answer == "Facts [1][2]."
    assert [c.locator_value for c in grouped.citations] == ["2", "1"]
    math = audit_citations("Vector $[1, 2]$, code `[1, 2]` and facts [2, 1].", chunks)
    assert "$[1, 2]$" in math.answer and "`[1, 2]`" in math.answer
    assert [c.locator_value for c in math.citations] == ["2", "1"]
    print("PASS: grouped references validated and renumbered individually")

    unsupported = "A supported fact [1].\n\nHere is a lengthy invented example that adds information not present in any uploaded source."
    assert has_uncited_prose(unsupported)
    assert not has_uncited_prose("The cost is 400 [1].\n\n$$120 + 80 + 200 = 400$$")
    assert not has_uncited_prose("## Heading\n\nA supported explanation with enough words to make the paragraph substantial and complete [1].")
    service = object.__new__(ChatService)
    chat = NS(id="chat", project_id="project", title="New chat", summary="")
    service.settings = NS(memory_chat_window=10, top_k=6, max_distance=.8, memory_context_chars=6000)
    service.chats = NS(get_owned=lambda *_: chat, recent_messages=lambda *a, **k: [], add_message=Mock(), rename=Mock())
    service.sources = NS(ready_ids=lambda _: ["source"])
    service.embedder = NS(embed=lambda _: [[1., 0.]])
    service.store = NS(query=Mock(return_value=[chunk("A supported fact.")]))
    service._rewrite = lambda q, _: (q, False)
    service._blocks = lambda *_: ("", "")
    service._attach_retrieved_images = lambda m, c: m
    service._attach_retrieved_audio = lambda m, c: m
    service._after_turn = Mock()
    service._generate_title = Mock(side_effect=AssertionError("No title API call"))
    service.llm = NS(chat=Mock(side_effect=[unsupported, "A supported fact [1]."]))
    result = service.send("user", "chat", "Explain")
    assert result["answer"] == "A supported fact [1]." and service.llm.chat.call_count == 2
    service._generate_title.assert_not_called()
    service.llm.chat = Mock(side_effect=[unsupported, unsupported])
    assert service.send("user", "chat", "Explain")["answer"] == REFUSAL
    print("PASS: repair uncited paragraphs once; refuse if repair remains unsupported")

    assert rate_limit_delay(quota()) == 39.5
    assert rate_limit_delay(quota("cooldown", **{"retry-after": "12"})) == 12
    assert rate_limit_delay(quota("{'retryDelay': '22s'}")) == 22
    assert rate_limit_delay(quota("insufficient_quota")) is None
    assert rate_limit_delay(quota("GenerateRequestsPerDayPerProject")) is None
    assert rate_limit_delay(quota("retry in 3600s")) is None
    client = object.__new__(LLMClient)
    create = Mock(side_effect=[quota(), "ok"])
    client._client = NS(chat=NS(completions=NS(create=create)))
    with patch("app.llm.time.sleep") as sleep:
        assert client._create(model="test") == "ok"
        assert 39 <= sleep.call_args.args[0] <= 40
    client._cooldown_until = 0
    create.side_effect = quota("insufficient_quota")
    create.reset_mock()
    with patch("app.llm.time.sleep") as sleep:
        try: client._create(model="test")
        except RateLimitError: pass
        else: raise AssertionError("Exhausted quota must surface")
        assert create.call_count == 1 and not sleep.called
    print("PASS: provider retry delay honored; daily/billing quota never retried")

    class BrokenStream:
        closed = False
        def __iter__(self):
            yield NS(choices=[NS(delta=NS(content="Partial"))])
            raise quota()
        def close(self): self.closed = True
    broken = BrokenStream()
    create.side_effect = None
    create.return_value = broken
    create.reset_mock()
    client.model, client.temperature, client.max_tokens = "test", 0, 32
    received = []
    try: client.chat([], on_chunk=received.append)
    except RateLimitError: pass
    else: raise AssertionError("Broken stream must fail")
    assert create.call_count == 1 and received == ["Partial"] and broken.closed
    print("PASS: partial streams are closed, never replayed with duplicated tokens")
    create.reset_mock()
    create.side_effect = [APIConnectionError(request=httpx.Request("POST", "https://example.test/v1")), "ok"]
    with patch("app.llm.time.sleep") as sleep:
        assert client._create(model="test") == "ok"
        assert create.call_count == 2 and sleep.call_args.args[0] == .5
    print("PASS: transient connection failures retain bounded retry behavior")

    judge = NS(complete_json=Mock(return_value={"claims": [{"supported": "false"}]}))
    assert judge_faithfulness(judge, "unsupported", "real source")[0] is None
    judge.complete_json.return_value = {"claims": [{"supported": False}, {"supported": True}]}
    assert judge_faithfulness(judge, "answer", "real source")[0] == .5
    assert "SOURCE EXCERPTS:\nreal source" in judge.complete_json.call_args.args[1]
    assert judge_faithfulness(judge, "answer", "")[0] is None
    print("PASS: judge receives evidence; missing/invalid judgments cannot score 100%")
    print("RAG RELIABILITY VERIFIED")


if __name__ == "__main__":
    main()
