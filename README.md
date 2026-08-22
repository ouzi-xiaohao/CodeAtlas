# CodeAtlas 研发知识图谱与变更影响分析平台

CodeAtlas 是面向研发团队的“研发知识中枢”。它把代码、技术文档、OpenAPI、数据库 DDL、Issue、Commit 与测试结果统一为可检索证据，并通过确定性工作流完成知识问答和跨服务变更影响分析。项目刻意不实现多模态、工单流转和多 Agent 调度。

## 已实现能力

- 研发知识问答：回答包含文件、符号、行号、Issue 等可追溯引用；无证据时拒绝推断。
- 变更影响分析：接受 PR 引用、Commit、Diff 或需求描述，提取文件/符号，查询两跳关系，输出影响项、历史 Issue、测试建议、风险等级及依据。
- 结构化索引：Python 使用 AST 按类/函数切分；Java 使用 Tree-sitter 提取类、方法、字段、调用及 Spring API；Markdown、OpenAPI、DDL 采用结构化切分。
- 混合检索：本地模式使用哈希向量与精确关键词检索；外部模式使用 Qdrant Dense + Sparse + RRF，并接入可替换 Reranker 和动态证据阈值，召回阶段执行仓库/团队 ACL 过滤。
- GraphRAG：Neo4j 保存服务、API、模块、函数、表、测试、Issue 等实体与关系，检索后仅扩展一至两跳。
- 增量索引：`/webhooks/git` 只删除并重建发生变化的文件片段。
- MCP：Repository、CI、Knowledge Graph 三个独立 Server。CI 写操作需要用户确认后签发的 5 分钟令牌。
- 真实 PR：读取 GitHub PR 元数据和分页文件 Patch，将标准化 Diff 直接送入影响分析；私有仓库通过只读 Token 接入。
- 可观测性：FastAPI 和工作流阶段均创建 OpenTelemetry span，覆盖查询改写、召回、图查询、影响分类与答案合成。
- 安全：路径越界防护、仓库内容按不可信数据处理、工具白名单、写操作确认、权限前置过滤和 PostgreSQL 审计事件。

## 架构

```text
HTTP / MCP
    │
    ├─ query:  查询改写 → ACL 混合检索 → 实体识别 → 1~2 跳图查询 → 证据压缩 → 带引用回答
    └─ impact: 读取变更 → 提取文件/符号 → RAG → 图路径 → 测试/Issue → 规则化风险判定

Local:    In-memory retrieval + graph（开箱即用，含 demo-commerce 示例）
External: Qdrant + Neo4j + PostgreSQL + Redis + OpenTelemetry（Docker Compose）
```

工作流位于 `app/services/workflow.py`，顺序固定且可测试，不由模型动态规划。LlamaIndex 负责句子感知的上下文切分与压缩，Qdrant/Neo4j 适配器位于 `app/services/external.py`。

## 快速启动

### 本地演示模式

```bash
python -m venv .venv
.venv/Scripts/activate
pip install ".[dev]"
copy .env.example .env
uvicorn app.main:app --reload
```

Windows 下若项目路径包含中文，建议使用普通安装 `pip install ".[dev]"`；部分 Python 发行版的 editable 安装会按系统代码页读取 `.pth`，从而无法识别 UTF-8 路径。

打开 `http://localhost:8000` 使用研发工作台前端，或访问 `http://localhost:8000/docs` 调试 API。应用启动时会加载 `demo-commerce` 数据，可以立即测试：

前端包含知识问答、变更影响分析和仓库索引三个界面，由 FastAPI 直接托管并调用同源 API，无需额外启动 Node.js 服务。

### 启用真实 Embedding 与 Cross-Encoder

默认 `hash + feature` 模式便于离线开发。使用真实中文向量和 Cross-Encoder 时，在 `.env` 中设置：

```dotenv
CODEATLAS_EMBEDDING_BACKEND=fastembed
CODEATLAS_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
CODEATLAS_EMBEDDING_DIMENSIONS=512
CODEATLAS_RERANKER_BACKEND=fastembed
CODEATLAS_RERANKER_MODEL=BAAI/bge-reranker-base
```

模型首次运行会下载到本地缓存。也可以只启用真实 Embedding，继续使用轻量特征 Reranker。

### 分析真实 GitHub Pull Request

公开仓库无需 Token；私有仓库需要配置只读 `CODEATLAS_GITHUB_TOKEN`。前端选择“Pull Request”后输入完整 GitHub PR URL，或调用：

```bash
curl -X POST http://localhost:8000/api/v1/impact \
  -H "Content-Type: application/json" \
  -d '{"input_type":"pull_request","value":"https://github.com/owner/repo/pull/123","repository":"repo"}'
```

系统会分页读取 PR 文件，标准化 Patch，提取 Java/Python 变更符号并执行现有的 RAG、图谱与风险分析工作流。

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -H "X-Teams: default" \
  -d '{"question":"订单创建失败可能涉及哪些模块？"}'

curl -X POST http://localhost:8000/api/v1/impact \
  -H "Content-Type: application/json" \
  -d '{"input_type":"description","value":"删除 orders.status 字段","repository":"demo-commerce"}'
```

索引当前配置的仓库根目录：

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -H "X-User-Id: alice" \
  -H "X-Teams: platform" \
  -H "X-Repositories: my-repo" \
  -d '{"repository":"my-repo","team":"platform","paths":["app","README.md"]}'
```

### 完整基础设施模式

复制 `.env.example` 为 `.env`，将 `CODEATLAS_REPOSITORY_ROOT` 设置为容器内的 `/workspace`，更换确认密钥，然后执行：

```bash
docker compose up --build
```

服务包括 FastAPI、PostgreSQL、Redis、Qdrant、Neo4j 和 OpenTelemetry Collector。Neo4j Browser 位于 `http://localhost:7474`，Qdrant 位于 `http://localhost:6333/dashboard`。

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/v1/query` | 带引用的研发知识问答 |
| `POST` | `/api/v1/impact` | PR/Commit/Diff/描述影响分析 |
| `POST` | `/api/v1/pull-requests/inspect` | 读取并标准化 GitHub PR 元数据与 Diff |
| `POST` | `/api/v1/ingest` | 全量或指定路径增量索引 |
| `POST` | `/api/v1/webhooks/git` | Git Webhook 增量索引 |
| `POST` | `/api/v1/confirmations` | 用户确认后签发写操作短时令牌 |
| `GET` | `/api/v1/audit` | 平台管理员查看审计事件 |
| `GET` | `/api/v1/health` | 健康状态和索引规模 |

权限通过 `X-User-Id`、`X-Teams` 和 `X-Repositories` 演示。生产环境应由 API Gateway 校验 JWT 后注入这些可信声明，不能直接信任公网客户端请求头。

## MCP Server

三个 Server 默认使用 Stdio：

```bash
python -m mcp_servers.repository
python -m mcp_servers.ci
python -m mcp_servers.knowledge_graph
```

- Repository MCP：`read_file`、`commit_details`、`compare_refs`，全部只读。
- CI MCP：`latest_test_report` 只读；`trigger_test_run` 必须携带与 action/resource 精确绑定的短时确认令牌。
- Knowledge Graph MCP：`find_entities`、`dependency_paths`、`module_owners`，最多两跳且不接受任意 Cypher。

写操作流程：先调用 `request_test_run` 获取 action/resource，可信 UI 展示完整操作；用户确认后调用 `/api/v1/confirmations`（请求头 `X-User-Confirmed: true`）；最后把令牌交给 `trigger_test_run`。令牌超时或绑定信息不一致都会被拒绝。

## 测试与质量门槛

```bash
pytest -q
ruff check app mcp_servers tests
```

当前测试覆盖 ACL 检索、两跳边界、AST 切分、路径越界和破坏性 Schema 变更风险。建议后续加入三类离线评测集：架构问答、代码定位、影响分析，并持续统计 Recall@K、MRR、引用准确率、忠实度和 P95 端到端延迟。

仓库已提供小型可复现基准：

```bash
python -m evaluation.run_eval
```

详细定义和局限见 `evaluation/README.md`。该基准使用内存检索与合成数据，不能替代真实仓库上的人工标注评测。

## 生产化下一步

本仓库是可运行 MVP。生产部署还应接入真实代码平台的 Webhook/PR API、组织级 OIDC、企业 Embedding/Reranker、Redis 一次性确认令牌，以及针对各语言的 Tree-sitter 解析器。任何仓库注释或文档只作为检索数据进入提示上下文，不得改变工具白名单或确认策略。
