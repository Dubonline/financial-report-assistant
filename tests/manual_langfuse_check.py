"""真实 Langfuse SDK + 真实 Agent 图，离线模型与内存导出器；不发送业务数据。"""

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langfuse import Langfuse
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import agent_factory, services, streaming, tracing
from app.config import get_settings
from app.main import app
from app.prompts.defaults import RESEARCHER_SYSTEM_PROMPT
import manual_web_check as web


class MeteredModel(web.OfflineModel):
    """已知用量用来检验 SDK 采集，不用离线数据推算真实供应商账单。"""
    @property
    def _identifying_params(self):
        return {"model_name": "offline-metered"}

    def response(self, messages):
        if "模拟模型故障" in str(messages[-1].content):
            raise RuntimeError("offline failure")
        role = self.role
        if role == "main" and RESEARCHER_SYSTEM_PROMPT in str(messages[0].content):
            return web.OfflineModel.response(self.model_copy(update={"role": "researcher"}), messages)
        return super().response(messages)

    async def _astream(self, *args, **kwargs):
        async for chunk in super()._astream(*args, **kwargs):
            yield chunk
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        ))


def attrs(span):
    return dict(span.attributes or {})


def main():
    assert tracing._client is None
    exporter = InMemorySpanExporter()
    original_init = Langfuse.__init__

    def local_init(self, **kwargs):
        original_init(self, **kwargs, span_exporter=exporter)

    with TemporaryDirectory(prefix="report-trace-check-") as temp, ExitStack() as stack:
        settings = get_settings().model_copy(update={
            "langfuse_enabled": True,
            "langfuse_capture_content": False,
            "langfuse_public_key": "pk-lf-offline-" + uuid4().hex,
            "langfuse_secret_key": "sk-lf-offline",
            "checkpoint_db_path": str(Path(temp) / "checkpoints.db"),
            "store_db_path": str(Path(temp) / "store.db"),
            "log_dir": str(Path(temp) / "logs"),
            "sse_heartbeat_seconds": 0.1,
        })
        for module in [agent_factory, services, streaming, tracing]:
            stack.enter_context(patch.object(module, "get_settings", return_value=settings))
        stack.enter_context(patch.object(agent_factory, "build_model", side_effect=lambda: MeteredModel(role="main")))
        stack.enter_context(patch.object(agent_factory, "build_reviewer_model", side_effect=lambda: MeteredModel(role="reviewer")))
        stack.enter_context(patch.object(app.router, "lifespan_context", web.test_lifespan))
        with patch.object(Langfuse, "__init__", local_init):
            tracing.initialize_tracing()
        try:
            # 使用已有端到端断言，覆盖两个子 Agent、计算、停止、断连、会话与历史。
            web.check_http()
            asyncio.run(web.check_disconnect())
            with TestClient(app) as client:
                sid = client.post("/v1/sessions", json={}).json()["session_id"]
                response = client.post("/v1/agent/stream", json={"session_id": sid, "question": "模拟模型故障"})
                events = web.parse_sse(response.text)
                assert events[-1][1]["status"] == "error", events
                assert len({data["trace_id"] for event, data in events if event in {"run_start", "error", "done"}}) == 1
            tracing._client.flush()
            spans = exporter.get_finished_spans()
            roots = [s for s in spans if s.name == "financial-report-request"]
            assert roots, "未导出请求 root"
            root_states = {json.loads(attrs(s).get("langfuse.observation.metadata.status", "null")) for s in roots}
            assert {"success", "cancelled", "disconnected", "error"} <= root_states, root_states
            assert len({s.context.trace_id for s in roots}) == len(roots), "不同轮次混入同一 Trace"
            successful = next(s for s in roots if json.loads(attrs(s).get("langfuse.observation.metadata.status", "null")) == "success")
            tree = [s for s in spans if s.context.trace_id == successful.context.trace_id]
            names = {s.name for s in tree}
            assert {"financial-report-main", "researcher", "reviewer", "safe_calculate", "task"} <= names, names
            by_id = {s.context.span_id: s for s in tree}
            assert all(s.parent and s.parent.span_id in by_id for s in tree if s is not successful), "Trace 父子关系断裂"
            generations = [s for s in tree if attrs(s).get("langfuse.observation.type") == "generation"]
            assert generations
            ancestor_names = set()
            for generation in generations:
                current = generation
                while current.parent and current.parent.span_id in by_id:
                    current = by_id[current.parent.span_id]
                    ancestor_names.add(current.name)
            assert {"financial-report-main", "researcher", "reviewer"} <= ancestor_names, ancestor_names
            for generation in generations:
                a = attrs(generation)
                usage = json.loads(a["langfuse.observation.usage_details"])
                assert usage["input"] == 11 and usage["output"] == 7, usage
                assert a["langfuse.observation.model.name"] == "offline-metered", a
                assert generation.end_time >= generation.start_time
            for span in spans:
                a = attrs(span)
                assert "langfuse.observation.input" not in a and "langfuse.observation.output" not in a
                assert a.get("user.id") == "dubonline", a
                assert a.get("session.id"), a
            print(f"PASS：{len(roots)} 次独立请求，主图/子图/工具父子关系、用量、模型名、耗时与正文屏蔽")

            # 两个同时执行的独立请求不共享 CallbackHandler 或父 span。
            async def concurrent():
                async def run(index):
                    with tracing.trace_request({}, request_id=f"parallel-{index}", session_id=f"session-{index}", user_id="dubonline") as item:
                        await asyncio.sleep(0)
                        assert len(item.config["callbacks"]) == 1
                        item.status = "success"
                        return item.trace_id, item.config["callbacks"][0]
                first, second = await asyncio.gather(run(1), run(2))
                assert first[0] != second[0] and first[1] is not second[1]
            asyncio.run(concurrent())
            with patch("langfuse.langchain.CallbackHandler", side_effect=RuntimeError("trace unavailable")):
                with tracing.trace_request({}, request_id="failed-setup", session_id="s", user_id="dubonline") as item:
                    assert item.config == {} and item.trace_id is None
            print("PASS：并发请求回调隔离与追踪初始化故障降级")
            settings.langfuse_capture_content = True
            with tracing.trace_request({}, request_id="content-opt-in", session_id="s", user_id="dubonline") as item:
                item.observation.update(input={"question": "visible", "secret_key": "private", "text": settings.langfuse_secret_key})
                item.status = "success"
            tracing._client.flush()
            content_span = next(s for s in exporter.get_finished_spans() if s.context.trace_id == int(item.trace_id, 16))
            content = json.loads(attrs(content_span)["langfuse.observation.input"])
            assert content == {"question": "visible", "secret_key": "***", "text": "***"}, content
            settings.langfuse_capture_content = False
            generation = tracing._client.start_observation(name="no-usage", as_type="generation", model="offline-unknown")
            generation.end()
            tracing._client.flush()
            no_usage = next(s for s in exporter.get_finished_spans() if s.name == "no-usage")
            assert "langfuse.observation.usage_details" not in attrs(no_usage)
            assert "langfuse.observation.cost_details" not in attrs(no_usage)
            # exporter 失败时服务仍需完成，不能将统计故障升级为问答故障。
            with patch.object(exporter, "export", return_value=SpanExportResult.FAILURE), TestClient(app) as client:
                sid = client.post("/v1/sessions", json={}).json()["session_id"]
                response = client.post("/v1/agent/invoke", json={"session_id": sid, "question": "复核20至25的增长率"})
                assert response.status_code == 200 and response.json()["trace_id"]
                tracing._client.flush()
            print("PASS：正文显式开启及密钥遮蔽、缺失 usage 不伪造零值、导出失败不阻断问答")
        finally:
            asyncio.run(tracing.shutdown_tracing())

        with patch.object(tracing, "get_settings", return_value=settings.model_copy(update={"langfuse_enabled": False})):
            tracing.initialize_tracing()
            with tracing.trace_request({}, request_id="disabled", session_id="s", user_id="dubonline") as item:
                assert item.trace_id is None and item.config == {}
        with patch.object(tracing, "get_settings", return_value=settings.model_copy(update={"langfuse_secret_key": ""})):
            try:
                tracing.initialize_tracing()
            except RuntimeError:
                pass
            else:
                raise AssertionError("启用但缺凭证未报错")
        print("PASS：关闭追踪零初始化、缺凭证拒绝启用、退出释放")
    print("离线集成检查通过；成本由真实 Langfuse 服务端模型价格计算，未验证远程入库与真实账单。")


if __name__ == "__main__":
    main()
