from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.guardrails import AgentGuardrails, GuardrailViolation
from app.agent.instructions import QUERY_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from app.agent.memory import AgentMemory
from app.agent.observability import AgentTracer
from app.agent.tools import KnowledgeSearchTool
from app.api_models import AgentAnswer
from app.llm import get_provider_runtime


class AgentRunner:
    """Main agent orchestrator — dispatches to sub-agents (Lint, Push, Graph) and handles Q&A."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.guardrails = AgentGuardrails()
        self.memory = AgentMemory()
        self.tracer = AgentTracer()

    async def answer_question(
        self,
        question: str,
        provider_id: str | None = None,
        model: str | None = None,
        model_profile_name: str | None = None,
    ) -> AgentAnswer:
        run_id = f"run-{uuid4().hex}"
        try:
            self.guardrails.validate_user_input(question)
        except GuardrailViolation as exc:
            answer = str(exc)
            self.tracer.log("guardrail_block", {"run_id": run_id, "question": question, "reason": answer})
            return AgentAnswer(run_id=run_id, answer=answer, sources=[], model=model or "", provider=provider_id or "")

        # Route to sub-agents if intent matches
        intent = self._detect_intent(question)
        if intent == "lint":
            return await self._run_lint(run_id, question, provider_id, model, model_profile_name)
        if intent == "push":
            return await self._run_push(run_id, question, provider_id, model, model_profile_name)
        if intent == "graph":
            return await self._run_graph(run_id, question, provider_id, model, model_profile_name)
        if intent == "sync":
            return await self._run_sync(run_id, question, provider_id, model, model_profile_name)

        # Default: knowledge Q&A
        search = KnowledgeSearchTool(self.db).run(question)
        provider, resolved_model, provider_config = get_provider_runtime(provider_id, model)
        resolved_provider = provider.provider_name
        self.tracer.log(
            "agent_start",
            {
                "run_id": run_id,
                "provider": resolved_provider,
                "model": resolved_model,
                "profile": model_profile_name,
                "tool_results": [search.metadata],
            },
        )

        if provider_config.get("api_style") != "mock" and not provider_config.get("base_url"):
            answer = "当前模型供应商还没有配置 Base URL。请先到设置页补全模型接口。"
        else:
            try:
                prompt = f"{QUERY_INSTRUCTIONS}\n\n本地资料：\n{search.content}\n\n用户问题：{question}"
                answer = await provider.chat(
                    [
                        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                        {"role": "user", "content": prompt},
                    ],
                    model=resolved_model,
                    temperature=0.2,
                )
            except Exception as exc:
                answer = (
                    "StarMind 已经完成 Agent 框架调用，但模型没有成功返回。"
                    "请确认 DeepSeek API Key 已保存且网络可访问。"
                    f"\n\n错误：{type(exc).__name__}: {exc}"
                )

        self.memory.remember_run(run_id, question, answer)
        self.tracer.log("agent_finish", {"run_id": run_id, "answer_preview": answer[:500]})
        sources = search.metadata.get("items", [])
        return AgentAnswer(
            run_id=run_id,
            answer=answer,
            sources=sources,
            model=resolved_model,
            provider=resolved_provider,
            profile=model_profile_name,
        )

    # --- Sub-agent dispatch ---

    def _detect_intent(self, question: str) -> str | None:
        q = question.lower()
        if any(kw in q for kw in ["检查", "lint", "健康", "诊断", "孤立", "过期", "重复"]):
            return "lint"
        if any(kw in q for kw in ["推送", "push", "推荐", "今日推荐"]):
            return "push"
        if any(kw in q for kw in ["图谱", "星链", "关联", "graph", "连接关系"]):
            return "graph"
        if any(kw in q for kw in ["同步", "sync", "采集", "抓取", "收藏夹"]):
            return "sync"
        return None

    async def _run_lint(self, run_id: str, question: str, provider_id, model, profile) -> AgentAnswer:
        from app.agent.lint_agent import LintAgent
        self.tracer.log("sub_agent_dispatch", {"run_id": run_id, "agent": "lint"})
        report = await LintAgent(self.db).run_full_check()
        findings = report.get("findings", [])
        if not findings:
            answer = "✅ 知识库健康检查通过，没有发现问题。"
        else:
            lines = [f"🔍 知识库检查发现 {len(findings)} 个问题：\n"]
            for f in findings[:10]:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(f["severity"], "•")
                lines.append(f"{icon} **{f['check_type']}** — {f['message']}")
                lines.append(f"  → {f['suggestion']}")
            if len(findings) > 10:
                lines.append(f"\n...还有 {len(findings) - 10} 个问题")
            answer = "\n".join(lines)
        self.memory.remember_run(run_id, question, answer)
        return AgentAnswer(run_id=run_id, answer=answer, sources=[], model=model or "", provider=provider_id or "", profile=profile)

    async def _run_push(self, run_id: str, question: str, provider_id, model, profile) -> AgentAnswer:
        from app.services.push_service import PushService
        self.tracer.log("sub_agent_dispatch", {"run_id": run_id, "agent": "push"})
        items = await PushService(self.db).generate_push()
        if not items:
            answer = "当前没有可推送的内容。可能是推送已暂停、不在推送时间窗口内、或知识库为空。"
        else:
            lines = ["📌 **今日推荐**\n"]
            for item in items:
                lines.append(f"- 《{item['title']}》— {item['platform']}")
            answer = "\n".join(lines)
        self.memory.remember_run(run_id, question, answer)
        return AgentAnswer(run_id=run_id, answer=answer, sources=[], model=model or "", provider=provider_id or "", profile=profile)

    async def _run_graph(self, run_id: str, question: str, provider_id, model, profile) -> AgentAnswer:
        from app.services.graph_service import GraphService
        self.tracer.log("sub_agent_dispatch", {"run_id": run_id, "agent": "graph"})
        data = GraphService(self.db).get_graph_data()
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        orphans = GraphService(self.db).detect_orphans()
        lines = [
            f"🌐 **知识图谱概览**\n",
            f"- 节点数：{len(nodes)}",
            f"- 连接数：{len(edges)}",
            f"- 孤立节点：{len(orphans)} 个",
        ]
        if edges:
            domains = set(n.get("domain") for n in nodes)
            lines.append(f"- 覆盖领域：{', '.join(sorted(d for d in domains if d))}")
        lines.append("\n打开 [知识星链页面](/ui/graph) 查看可视化图谱。")
        answer = "\n".join(lines)
        self.memory.remember_run(run_id, question, answer)
        return AgentAnswer(run_id=run_id, answer=answer, sources=[], model=model or "", provider=provider_id or "", profile=profile)

    async def _run_sync(self, run_id: str, question: str, provider_id, model, profile) -> AgentAnswer:
        from app.models import Connector, SyncLedgerItem
        self.tracer.log("sub_agent_dispatch", {"run_id": run_id, "agent": "sync"})
        connectors = self.db.query(Connector).all()
        ledger_count = self.db.query(SyncLedgerItem).count()
        auto_enabled = [c for c in connectors if c.auto_sync_enabled]
        lines = [
            f"📡 **同步状态**\n",
            f"- 已连接平台：{len(connectors)} 个",
            f"- 自动同步已启用：{len(auto_enabled)} 个",
            f"- Sync Ledger 总记录：{ledger_count} 条",
        ]
        for c in connectors[:5]:
            status = "✅ 自动同步" if c.auto_sync_enabled else "手动"
            lines.append(f"  - {c.name} ({c.platform}) — {status}")
        lines.append("\n如需手动触发同步，请到 [连接来源](/ui/connectors) 页面操作。")
        answer = "\n".join(lines)
        self.memory.remember_run(run_id, question, answer)
        return AgentAnswer(run_id=run_id, answer=answer, sources=[], model=model or "", provider=provider_id or "", profile=profile)
