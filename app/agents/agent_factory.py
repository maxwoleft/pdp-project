"""Фабрика агентів — по одному інстансу на країну, кешуються."""
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm.base import LLMClient
from app.adapters.llm.hybrid_router import HybridLLMRouter
from app.agents.sales_agent import SalesAgent
from app.agents.tools.builder import build_tool_registry

INSTRUCTIONS_DIR = Path(__file__).parent / "instructions"


class AgentFactory:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient | None = None,
        embedder=None,
        query_rewriter=None,
        llm_reranker=None,
        redis=None,
    ) -> None:
        self._llm = llm or HybridLLMRouter()
        self._session_factory = session_factory
        self._embedder = embedder
        self._query_rewriter = query_rewriter
        self._llm_reranker = llm_reranker
        self._redis = redis
        self._cache: dict[str, SalesAgent] = {}

    def get_agent(self, country: str) -> SalesAgent:
        country = country.lower()
        if country in self._cache:
            return self._cache[country]

        instructions = self._load_instructions(country)
        tool_registry = build_tool_registry(
            country,
            self._session_factory,
            embedder=self._embedder,
            query_rewriter=self._query_rewriter,
            llm_reranker=self._llm_reranker,
            redis=self._redis,
        )
        agent = SalesAgent(
            country=country,
            system_instructions=instructions,
            llm=self._llm,
            tool_registry=tool_registry,
        )
        self._cache[country] = agent
        return agent

    # Порядок склеювання модулів інструкцій.
    # CRITICAL: sales_dna ОДРАЗУ після persona — це найважливіше правило поведінки.
    INSTRUCTION_MODULES: tuple[str, ...] = (
        "persona.md",
        "sales_dna.md",
        "golden_rules.md",
        "booking_flow.md",
        "cancellation_flow.md",
        "communication_style.md",
        "tools_reference.md",
    )

    def _load_instructions(self, country: str) -> str:
        parts: list[str] = []
        for module in self.INSTRUCTION_MODULES:
            path = INSTRUCTIONS_DIR / module
            if path.exists():
                parts.append(path.read_text(encoding="utf-8").strip())

        override_path = INSTRUCTIONS_DIR / "country_overrides" / f"{country}.md"
        if override_path.exists():
            parts.append(override_path.read_text(encoding="utf-8").strip())

        return "\n\n---\n\n".join(parts)
