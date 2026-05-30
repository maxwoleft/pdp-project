"""Eval runner — запускає сценарії через реальний агент і оцінює через LLM judge.

Архітектура:
    Scenario → Conversation → Agent.respond() (× N turns) → Transcript
        ↓
    LLM Judge (gpt-4o-mini) — порівнює transcript з expectations
        ↓
    Pass/Fail per expectation + summary

Запуск:
    python -m scripts.eval.run                          # всі сценарії
    python -m scripts.eval.run --scenario booking_full_flow
    python -m scripts.eval.run --tag search             # тільки з тегом
    python -m scripts.eval.run --country ua

Витрати: ~$0.02-0.05 на повний прогон (15 сценаріїв × 1-3 турни × Claude calls
+ 15 judge calls на gpt-4o-mini). Запускати на demand перед merge / release.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass

from openai import AsyncOpenAI
from redis.asyncio import Redis

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.adapters.llm.hybrid_router import HybridLLMRouter
from app.adapters.llm.query_rewriter import LLMReranker, QueryRewriter
from app.agents.agent_factory import AgentFactory
from app.core.config import get_settings
from app.domain.models.conversation import Conversation, ConversationTurn
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.eval.db_loader import load_scenarios_from_db
from scripts.eval.scenarios import Scenario, filter_scenarios

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("eval")
log.setLevel(logging.INFO)

JUDGE_MODEL = "gpt-4o-mini"
JUDGE_TIMEOUT_S = 30.0


# ── Judge ─────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """You are an evaluator for an AI sales agent at a beauty salon.

You receive a TRANSCRIPT of a conversation and two checklists:
- EXPECTATIONS: behaviors the agent MUST exhibit (good)
- MUST_NOT: behaviors the agent MUST NOT exhibit (bad / red flags)

The transcript includes both assistant text AND tool calls (marked [TOOL CALLS]).
Use BOTH when judging — a tool call counts as evidence the agent did something.

EVALUATION RULES:

1. PURPOSE OVER WORDING. Expectations describe INTENT, not literal phrasing.
   - "calls search_services" → pass if ANY search_services call appears anywhere in tool calls
   - "shows price" → pass if assistant mentions any number with currency (грн/PLN/£/euro)
   - "asks about X" → pass if any question that semantically asks about X appears

2. PARTIAL CREDIT. If transcript ends mid-flow (only 1-3 turns), forward-looking
   expectations pass if the agent is clearly heading in the right direction.

3. CLARIFY ≠ FAIL. If the agent asks a clarifying question instead of immediately
   acting, that's GOOD behavior (it's prescribed in the booking flow). Don't penalize.

4. TOOL CALLS ARE EVIDENCE. If expectation says "calls search_services" and
   [TOOL CALLS] section contains "search_services(...)" — that's a PASS, even if
   the assistant text doesn't mention search.

5. MUST_NOT is strict. If a red flag is present anywhere — fail.

Return ONLY valid JSON:
{
  "expectations": [{"text": "<expectation text>", "pass": true/false, "reason": "<one sentence>"}],
  "must_not": [{"text": "<must_not text>", "pass": true/false, "reason": "<one sentence>"}],
  "overall_pass": true/false
}

overall_pass = true ONLY if ALL expectations pass AND ALL must_not pass."""


@dataclass
class JudgeResult:
    overall_pass: bool
    expectations: list[dict]  # [{text, pass, reason}, ...]
    must_not: list[dict]
    raw_response: str = ""


def _clean_for_judge(text: str, max_len: int = 2000) -> str:
    """Очищає content від керуючих символів та обрізає до max_len."""
    if not text:
        return ""
    # Видаляємо керуючі символи (окрім \n \t)
    cleaned = "".join(
        c for c in text
        if c == "\n" or c == "\t" or (c.isprintable() or c.isspace())
    )
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + " ...[truncated]"
    return cleaned


async def judge_transcript(
    client: AsyncOpenAI,
    scenario: Scenario,
    transcript: list[dict],
) -> JudgeResult:
    """LLM judge: порівнює transcript із сценарієм. Гарантовано не падає."""
    transcript_text = "\n\n".join(
        f"[{t['role'].upper()}] {_clean_for_judge(t['content'])}" for t in transcript
    )
    user_msg = (
        f"SCENARIO: {scenario.description}\n\n"
        f"EXPECTATIONS (must do):\n"
        + "\n".join(f"- {e}" for e in scenario.expectations)
        + "\n\nMUST_NOT (red flags):\n"
        + "\n".join(f"- {m}" for m in scenario.must_not)
        + "\n\nTRANSCRIPT:\n"
        + transcript_text
    )
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=1500,
                temperature=0.0,
            ),
            timeout=JUDGE_TIMEOUT_S,
        )
        content = (resp.choices[0].message.content or "").strip()
        data = json.loads(content)
        return JudgeResult(
            overall_pass=bool(data.get("overall_pass", False)),
            expectations=data.get("expectations", []),
            must_not=data.get("must_not", []),
            raw_response=content,
        )
    except Exception as exc:
        log.warning("Judge failed for %s: %s", scenario.id, exc)
        return JudgeResult(
            overall_pass=False,
            expectations=[{"text": e, "pass": False, "reason": f"judge error: {exc}"} for e in scenario.expectations],
            must_not=[{"text": m, "pass": False, "reason": "judge error"} for m in scenario.must_not],
        )


# ── Runner ────────────────────────────────────────────────────────

async def run_scenario(
    scenario: Scenario,
    agent_factory: AgentFactory,
    judge_client: AsyncOpenAI,
) -> tuple[JudgeResult, list[dict], float]:
    """Запускає один сценарій: створює діалог → робить N turns → judge.

    Перехоплює tool calls агента, щоб judge міг оцінити implementation
    (які tools викликались, з якими параметрами), а не тільки текст відповіді.
    """
    agent = agent_factory.get_agent(scenario.country)

    # Перехоплюємо tool execute для logging
    original_execute = agent._tools.execute
    captured_calls: list[dict] = []

    async def capturing_execute(name, tool_input):
        captured_calls.append({"name": name, "input": dict(tool_input or {})})
        return await original_execute(name, tool_input)

    agent._tools.execute = capturing_execute  # type: ignore

    try:
        conversation = Conversation(
            conversation_id=f"eval:{scenario.id}",
            country=scenario.country,
            channel="eval",
            salon_id=scenario.preset_salon_id,
        )

        transcript: list[dict] = []
        t0 = time.time()

        for user_msg in scenario.turns:
            # Очищаємо буфер tool calls для цього turn
            calls_before = len(captured_calls)
            new_user_content = [{"type": "text", "text": user_msg}]
            try:
                assistant_text = await agent.respond(conversation, new_user_content)
            except Exception as exc:
                assistant_text = f"[AGENT ERROR: {exc}]"

            # Збираємо tool calls що сталися в цьому turn
            turn_tool_calls = captured_calls[calls_before:]
            tool_summary = ""
            if turn_tool_calls:
                tool_lines = []
                for tc in turn_tool_calls:
                    inp = tc.get("input", {})
                    # Compact представлення params (тільки ключові)
                    params_short = {
                        k: (v if isinstance(v, (str, int, float, bool)) else str(type(v).__name__))
                        for k, v in inp.items()
                        if k in ("query", "salon_id", "service_id", "category_id", "gender", "city", "date_from")
                    }
                    tool_lines.append(f"  {tc['name']}({params_short})")
                tool_summary = "\n[TOOL CALLS]\n" + "\n".join(tool_lines)

            conversation.history.append(ConversationTurn(role="user", content=user_msg))
            conversation.history.append(ConversationTurn(role="assistant", content=assistant_text))
            transcript.append({"role": "user", "content": user_msg})
            transcript.append({
                "role": "assistant",
                "content": assistant_text + tool_summary,
            })

        elapsed = time.time() - t0
        judge_result = await judge_transcript(judge_client, scenario, transcript)
        return judge_result, transcript, elapsed
    finally:
        # Відновлюємо оригінальний executor (агент кешується!)
        agent._tools.execute = original_execute  # type: ignore


# ── CLI ───────────────────────────────────────────────────────────

def print_scenario_result(
    scenario: Scenario,
    result: JudgeResult,
    transcript: list[dict],
    elapsed: float,
    verbose: bool,
) -> None:
    status = "PASS" if result.overall_pass else "FAIL"
    color = "\033[32m" if result.overall_pass else "\033[31m"
    reset = "\033[0m"
    print(f"{color}[{status}]{reset} {scenario.id} ({elapsed:.1f}s) — {scenario.description}")

    if not result.overall_pass or verbose:
        for exp in result.expectations:
            mark = "+" if exp.get("pass") else "-"
            print(f"      [{mark}] {exp.get('text', '')[:90]}")
            if not exp.get("pass") and exp.get("reason"):
                print(f"          ↳ {exp['reason']}")
        for mn in result.must_not:
            mark = "+" if mn.get("pass") else "-"
            print(f"      [{mark}] NOT: {mn.get('text', '')[:85]}")
            if not mn.get("pass") and mn.get("reason"):
                print(f"          ↳ {mn['reason']}")

    if verbose:
        print("      --- Transcript ---")
        for t in transcript:
            content = t["content"][:200].replace("\n", " ")
            print(f"      {t['role'][:1].upper()}: {content}")
        print()


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", help="Run only this scenario id")
    parser.add_argument("--tag", help="Filter by tag")
    parser.add_argument("--country", help="Filter by country (ua/pl/gb)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show transcripts and details for all")
    parser.add_argument("--no-rerank", action="store_true", help="Disable LLM reranker")
    args = parser.parse_args()

    # Load scenarios from DB (with hardcoded fallback)
    all_scenarios = await load_scenarios_from_db()

    # Filter
    if args.scenario:
        scenarios = [s for s in all_scenarios if s.id == args.scenario]
        if not scenarios:
            print(f"Scenario not found: {args.scenario}")
            return
    else:
        scenarios = all_scenarios
        if args.tag:
            scenarios = [s for s in scenarios if args.tag in s.tags]
        if args.country:
            scenarios = [s for s in scenarios if s.country == args.country]

    print(f"Running {len(scenarios)} scenarios...\n")

    # Build infrastructure (як у main.py)
    settings = get_settings()
    engine = build_engine()
    session_factory = build_session_factory(engine)
    redis = Redis.from_url(settings.redis_url)
    embedder = OpenAIEmbedder()
    rewriter = QueryRewriter(redis=redis)
    reranker = LLMReranker(redis=redis, enabled=not args.no_rerank)
    llm = HybridLLMRouter()

    agent_factory = AgentFactory(
        session_factory=session_factory,
        llm=llm,
        embedder=embedder,
        query_rewriter=rewriter,
        llm_reranker=reranker,
    )
    judge_client = AsyncOpenAI(api_key=settings.openai_api_key)

    # Run scenarios sequentially (для чистого виводу).
    # Якщо сценарій зафейлив — робимо 1 retry, бо LLM іноді має transient issues
    # (особливо empty responses при коротких prompts). Це знижує флакі-фактор.
    passed = 0
    failed = 0
    failed_ids: list[str] = []
    total_t0 = time.time()
    MAX_RETRIES = 2  # перша спроба + 1 retry

    for scenario in scenarios:
        result = None
        transcript: list[dict] = []
        elapsed = 0.0
        last_exc = None

        for attempt in range(MAX_RETRIES):
            try:
                result, transcript, elapsed = await run_scenario(
                    scenario, agent_factory, judge_client
                )
                if result.overall_pass:
                    break  # успіх — не retry
                # Fail: робимо retry якщо є спроби
                last_exc = None
            except Exception as exc:
                last_exc = exc
                result = None

        if result is None:
            print(f"\033[31m[ERROR]\033[0m {scenario.id}: {last_exc}")
            failed += 1
            failed_ids.append(scenario.id)
            continue

        print_scenario_result(scenario, result, transcript, elapsed, args.verbose)
        if result.overall_pass:
            passed += 1
        else:
            failed += 1
            failed_ids.append(scenario.id)

    total_elapsed = time.time() - total_t0
    total = passed + failed
    rate = passed * 100 // total if total else 0

    print()
    print("=" * 70)
    print(f"RESULTS: {passed}/{total} passed ({rate}%) in {total_elapsed:.1f}s")
    if failed_ids:
        print(f"FAILED: {', '.join(failed_ids)}")
    print()
    print(f"Rewriter stats: {rewriter.stats}")
    print(f"Reranker stats: {reranker.stats}")

    await engine.dispose()
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(amain())
