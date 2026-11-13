# 财报分析助手 · Financial Report Assistant

由 **dubonline** 持续探索与开发的财报研究项目。关注一个具体问题：如何让模型给出的数字分析，有原始证据、可执行的公式和明确的复核边界。

项目使用 FastAPI、DeepAgents / LangGraph 与 Vue 3，将财报片段检索、数值计算和按需复核组织为可交互的多 Agent 流程。当前为本地单用户、单进程版本，正在围绕证据质量、执行可控性和可观测性持续迭代。

## 可以做什么

- 用公司、年份和指标查询已导入的 FinQA 财报片段，保留表头、金额、单位说明和来源标识。
- 按指标覆盖度与关键词排序候选，同内容片段合并并保留各来源。
- 主 Agent 编排分析；researcher 使用只读查询工具取证；按要求调用 reviewer，基于交接材料独立复算。
- 通过受限 AST 算术工具执行加减乘除、增长率与占比计算，不执行任意 Python 代码。
- 通过 SSE 查看文本、工具和子 Agent 事件，停止生成，管理和恢复本地会话。
- 可选接入 Langfuse，追踪主图、子图、模型与工具，采集供应商返回的 Token 与耗时，并由服务端模型价格计算成本。

示例问题：

> 请查询 Apple 2014 年固定资产净额和 2013 年可比数值，列出原始数据、单位和出处，计算同比增长率，并独立复核。

当前数据为报告片段，不保证覆盖任意公司、完整年报或最新财报；未实现 PDF 上传、向量检索、自动财报采集或投资交易功能。复核调用和交接主要由提示词约束，reviewer 不重新查库，不能视为审计保证。

## 本地启动

需要 Python 3.12、Node.js 22.12+ 与 Yarn 1.22.22（可通过 Corepack 使用），以及实际可用的模型 API 账号。

```bash
git clone https://github.com/Dubonline/financial-report-assistant.git
cd financial-report-assistant
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell 对应使用 `py -3.12 -m venv .venv`、`.\.venv\Scripts\Activate.ps1` 和 `Copy-Item .env.example .env`。

在 `.env` 中填写自己的 `DEEPSEEK_API_KEY`。模型名称和地址需按账号实际支持的服务配置；若使用智谱复核模型，填写 `ZHIPU_API_KEY`；若希望复核回退到单独的 DeepSeek 实例，将 `REVIEWER_MODEL` 留空。不要提交真实密钥。

### 准备财报数据

仓库不包含本地数据库、对话历史或第三方数据集。自行从 [FinQA 官方仓库](https://github.com/czyssrs/FinQA) 获取所需的 `dataset/train.json`、`dev.json`、`test.json`，遵循其许可及数据使用要求。

```bash
python scripts/import_finqa.py --source-dir /path/to/FinQA/dataset
```

导入器读取目录中已有的 train/dev/test JSON，验证输入后创建财报与公司别名表。重复导入更新相同来源，不清空已有数据；不导入问题答案标签或用户会话。公司代码可直接用于查询，常见公司另有受控别名；公司名称未覆盖时请提供代码。

```bash
cd frontend
corepack yarn install --frozen-lockfile
cd ..
python start_all.py --check
python start_all.py
```

前端：<http://127.0.0.1:5173>；后端 API 文档：<http://127.0.0.1:8000/docs>。Ctrl+C 停止服务。启动脚本使用开发服务器，不用于公网部署。

### Langfuse（可选）

默认关闭，需要兼容 `langfuse==5.2.0` 的 Langfuse v4 服务或 Cloud 项目，服务端不在本仓库中部署。

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://127.0.0.1:3000
LANGFUSE_PUBLIC_KEY=pk-lf-your-project-key
LANGFUSE_SECRET_KEY=sk-lf-your-project-secret
LANGFUSE_CAPTURE_CONTENT=false
```

填写实际服务地址和项目密钥后重启。每轮 HTTP 请求独立返回 `trace_id`，通过会话编号关联多轮请求；控制台入口也接入追踪。正文默认不导出，需要查看提示词、证据或工具参数时显式开启 `LANGFUSE_CAPTURE_CONTENT`，并先确认外发数据范围。

Token 来自供应商 usage，中断时可能不完整。成本需要在 Langfuse Project Settings → Models 核对实际模型名与价格，包括缓存用量类型；缺失用量或价格不代表零成本。费用不是供应商账单。请求总耗时取根 span，不能将所有嵌套步骤的耗时重复相加。后台批量导出，退出时尽力发送缓冲；导出失败不阻断问答，但可能丢失追踪。

本轮实现不包含远程提示词管理、反馈评分或自动质量评测。机制参考 [官方 DeepAgents 集成](https://langfuse.com/integrations/frameworks/langchain-deepagents)与 [Token / 成本说明](https://langfuse.com/docs/observability/features/token-and-cost-tracking)。

## 验证

激活后端环境后执行：

```bash
python tests/test_import_finqa.py
python tests/manual_web_check.py
python tests/manual_langfuse_check.py
```

这些检查使用临时数据与离线模型，不调用真实模型。追踪检查使用真实 Langfuse SDK 与内存 exporter，覆盖父子关系、用量、终态、正文开关和导出失败降级；不代表已经验证你的远程服务入库或供应商账单。

导入官方数据后可运行 `python tests/manual_evidence_check.py`，只读核验代表性证据；该检查依赖 Apple 对应报告片段。`tests/manual_agent_stream.py` 与 `tests/test_company_catalog.py` 中的真实模型检查会消耗 Token，需自行决定运行。

```bash
cd frontend
corepack yarn test
corepack yarn build
```

## 实现与开发方向

我将“找到报告”“命中指标行”“证据足够计算”分开处理，并将计算执行与模型推理分离。多 Agent 不是正确性的保证，后续需要用对照评测验证其质量、延迟和成本收益。

- [架构与设计取舍](docs/ARCHITECTURE.md)
- [开发路线图](docs/ROADMAP.md)

欢迎通过 Issue 提供可复现问题和脱敏样例；请勿上传密钥、客户资料或真实会话记录。不宣称未经实测的准确率、性能或生产规模。

## 许可与数据来源

项目代码按 [MIT License](LICENSE) 分享。FinQA 数据、原始公司报告及第三方依赖适用各自许可，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。项目输出需人工核验，不构成投资建议。
