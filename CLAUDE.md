<!-- CLAUDE.md — 项目上下文文档，供 AI 助手参考 -->
<!-- 最后更新: 2026-06-06 -->

# 癫痫术前评估数据管理门户

## 项目概述

基于 Django 5.2 的癫痫患者全生命周期数据管理平台，支持患者 CRUD、多模态医学影像存储、AI 智能报告生成（Ollama + RAG + Agent）、知识库管理与向量检索。

## 技术栈

- **后端框架**: Django 5.2 + Django REST Framework
- **数据库**: SQLite (开发) / PostgreSQL (生产)
- **前端**: Argon Dashboard (Bootstrap 4), HTMX/AJAX offcanvas 模式
- **AI 推理**: Ollama (qwen2.5:7b 分析/自查, qwen2.5:14b 生成/修订)
- **向量数据库**: ChromaDB
- **文档导出**: python-docx (Word), ReportLab (PDF)
- **Python 版本**: 3.12+

## 项目结构关键路径

```
epilepsy_portal/                    # Django 项目根目录 (manage.py 所在)
├── epilepsy/                       # 核心业务应用
│   ├── models.py                   # Patient, FollowUp, OllamaServer, File models
│   ├── forms.py                    # PatientForm (超大表单), OllamaServerForm, UserWithRoleForm
│   ├── views.py                    # 主视图 (1270行+) 含 AI 报告生成
│   ├── views_helper.py             # build_patient_export_json() — AI 报告的数据源
│   ├── views_v1.py                 # 旧版视图 (向后兼容)
│   ├── urls.py                     # 业务路由
│   └── json.py                     # 自动生成的 PATIENT_GROUP_FIELDS + FIELDS_FOR_EXPORT
├── knowledge/                      # 知识库 & AI Agent (最新核心模块)
│   ├── agent_pipeline.py           # AgentPipeline 五步推理 (SSE)
│   ├── agent_prompts.py            # 4 个 Agent prompt 模板
│   ├── rag_prompts.py              # RAG 模式 prompt
│   ├── retriever.py                # 检索编排
│   ├── vector_store.py             # ChromaDB 封装
│   ├── chunker.py                  # 分块策略
│   ├── pipeline.py                 # 文档入库流水线
│   ├── ollama_client.py            # Ollama API 客户端
│   └── models.py                   # KnowledgeSettings, Document, Chunk, EditHistory
├── epilepsy_portal/                # Django 项目配置
│   ├── settings/                   # 多环境配置
│   ├── urls.py                     # 根路由
│   └── mixins.py                   # 通用 mixin
├── api/                            # REST API
├── templates/                      # 所有模板
│   ├── epilepsy/                   # 业务页面
│   └── knowledge/                  # 知识库页面
├── staticfiles/                    # 静态资源
└── generate_patient_detail_partial_fixed.py  # 自动生成详情模板
```

## 核心架构模式

### 1. 患者数据流 (AI 报告的数据路径)

```
Patient 模型 → PatientForm 表单数据
  → views_helper.build_patient_export_json(patient)
    → 结构化 JSON (sections → items → {label, value})
      → AI 报告生成的 prompt 上下文
```

### 2. 三种 AI 报告生成模式（SSE 流式）

| 模式 | URL 路由 | 函数 | Pipeline |
|------|---------|------|----------|
| 基础 | `generate-report/` | `patient_generate_report_stream` | `stream_ollama_report()` 直接调用 |
| RAG | `generate-report-rag/` | `patient_generate_report_stream_rag` | `retriever.retrieve_context()` → `stream_ollama_report()` |
| Agent | `generate-report-agent/` | `patient_generate_report_stream_agent` | `AgentPipeline(patient, server).execute()` |

### 3. Agent 五步流水线

```
AgentPipeline.execute() → SSE 事件生成器
├─ Step 1: _analyze()     → ollama_chat_json(model=think_model, prompt=ANALYSIS_SYSTEM_PROMPT)
├─ Step 2: _retrieve()    → retriever.retrieve_context(pseudo_export)
├─ Step 3: _draft()       → _stream_chat(model=generate_model)  # 流式
├─ Step 4: _critique()    → ollama_chat_json(model=think_model, prompt=CRITIQUE_SYSTEM_PROMPT)
└─ Step 5: _revise()      → _stream_chat(model=generate_model)  # 流式 (条件性)
```

SSE 事件类型: `progress`, `thinking`, `output`, `retrieval`, `error`, `done`

### 4. 知识库检索流程

```
上传文档 → extract_text() → chunk_text() → embeddings.embed() → ChromaDB.add()
                                                            ↓
查询时: build_query_text(export_json) → query_similar() → format_context_for_prompt()
```

### 5. 编辑反馈学习

```
用户编辑 AI 报告 → save_report_edit() → _compute_diff() → summarize_edit()
  → 若 learning_mode == "auto": ingest_markdown_report() → 入库
  → 若 learning_mode == "review": 等待管理员审核
```

## 关键文件与函数

### 数据导出 (AI 报告的数据源)
- `epilepsy/views_helper.py::build_patient_export_json(patient)` — 构建给 AI 的结构化 JSON
- `epilepsy/json.py::PATIENT_GROUP_FIELDS` — 表单字段分组配置（自动生成）
- `epilepsy/json.py::FIELDS_FOR_EXPORT` — 导出字段列表（自动生成）

### Ollama 通信
- `knowledge/ollama_client.py` — 以下函数的统一定义:
  - `ollama_chat_json()` — 非流式调用，返回 JSON（用于分析/自查步骤）
  - `ollama_chat()` — 非流式调用，返回文本
  - `summarize_edit()` — 编辑摘要
- `epilepsy/views.py::stream_ollama_report()` — 流式 SSE 调用

### 知识库配置
- `knowledge/models.py::KnowledgeSettings.load()` — 全局单例设置
  - `enable_rag` / `enable_agent` — RAG/Agent 功能开关
  - `top_k_cases` / `top_k_literature` — 检索数量
  - `chunk_strategy` — 分块策略
  - `learning_mode` — 编辑学习模式

### 模板生成器
- `patched_generate_patient_json.py` — AST 解析 models.py 生成字段配置
- `generate_patient_detail_partial_fixed.py` — 解析表单模板生成详情模板

## 重要约定

### 命名规范
- 表单字段中文 label 在 models.py 中作为模块级常量定义
- 所有面向用户的文本使用中文
- 文件类型使用小写: `mri`, `pet`, `eeg`, `seeg`
- 文档类型使用 snake_case: `case_report`, `edited_report`

### 权限检查
- 视图使用 `RoleRequiredMixin.allowed_roles` 或手动 `_require_staff()` 检查
- `epilepsy/models.py::UserRole` 三种角色: ADMIN, STAFF, GUEST
- 模板中使用 `user.profile.role` 控制 UI 显示

### AI 功能前提
- 所有 AI 功能依赖 `OllamaServer.objects.filter(is_enabled=True).first()`
- 思考模型 (think_model) 默认 qwen2.5:7b，生成模型默认 qwen2.5:14b
- Thinking 模式通过 `enable_thinking` + `think=True` payload 控制
- `<think>` 标签在 `AgentPipeline._split_thinking()` 和 `views._split_report_thinking_delta()` 中独立处理

### 自动生成文件 (勿手动编辑)
- `epilepsy/json.py` — 由 `patched_generate_patient_json.py` 生成
- `templates/epilepsy/patient_detail_partial.html` — 由 `generate_patient_detail_partial_fixed.py` 生成

## 运行环境

- **开发**: `python manage.py runserver`
- **Docker**: `docker-compose up`
- **Ollama**: 默认 `http://localhost:11434`，需预装 qwen2.5:7b, qwen2.5:14b, nomic-embed-text
- **ChromaDB**: 数据存储在 `epilepsy_portal/chroma_data/`，自动初始化

## 维护命令

```bash
# 修改 Patient 模型或表单后重新生成
python patched_generate_patient_json.py
python generate_patient_detail_partial_fixed.py

# 数据库迁移
python manage.py makemigrations
python manage.py migrate

# 重建知识库索引
python manage.py shell -c "from knowledge.pipeline import rebuild_index; print(rebuild_index())"
```

## 注意事项

1. **OllamaServer 单例管理**: `ollama_server_enable()` 视图确保同一时间仅一个服务启用
2. **大文件存储**: MRI/PET/EEG/sEEG 文件存储在 `settings.LARGE_FILE_BASE_DIR`，默认 `BASE_DIR/large_files/`
3. **CORS**: 开发环境 `DEBUG=True` 时宽松，生产环境需配置
4. **ChromaDB**: 使用 `chromadb.PersistentClient`，路径在 `vector_store.py` 中配置
5. **API 端点**: 部分功能通过 REST API 暴露（`api/` 应用），使用 DRF ViewSets
