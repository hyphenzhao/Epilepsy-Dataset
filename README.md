# 🧠 癫痫术前评估数据管理门户 (Epilepsy Surgery Dataset Portal)

基于 **Django 5.2** 的癫痫患者数据管理与 AI 辅助报告生成系统。支持患者全生命周期管理、多模态医学影像存储、AI 智能报告生成、知识库检索增强（RAG）、以及 Agent 多步推理流水线。

---

## ✨ 核心功能

### 1. 📊 全局总览（Dashboard）

- 服务器运行状态实时监控：
  - CPU 使用率
  - 内存使用率（已用 / 总量）
  - 磁盘使用率（已用 / 总量）
- 基于 Argon Dashboard 的卡片式 UI 布局

### 2. 🏥 患者管理

**新建 / 修改患者**
- 完整的癫痫患者信息表单，涵盖多个分区：
  - 基本信息（姓名、性别、生日、左右利手、科室、床号、入院时间）
  - 发作症状学（发作类型、频率、先兆、自动症等）
  - 影像学检查（MRI、PET 发现及文件上传）
  - 电生理检查（EEG、sEEG 发现及文件上传）
  - 神经心理评估
  - 手术信息
- 文件上传支持：MRI / PET / EEG / sEEG（含 MD5 + SHA-256 哈希校验）
- AJAX 侧滑面板（Offcanvas）编辑
- JavaScript 前端必填字段校验

**浏览患者**
- 列表展示所有患者基本信息
- 模糊搜索（姓名 / 科室 / 床号 / 关键词）
- 分页显示
- 单患者操作：修改（侧滑面板）、删除、查看详情
- 批量操作：批量下载信息、批量删除、批量下载文件

**患者详情**
- 按分区展示所有患者信息（自动从表单模板同步）
- MRI / PET / EEG / sEEG 图片预览网格
- 图片点击放大查看
- 文件管理与下载面板

**数据导出**
- JSON 格式导出（结构化完整数据）
- Excel 格式导出（表格化，含 verbose_name 中文列名）

### 3. 🔔 临床随访提醒（*新功能*）

- 自动随访计划：
  - 半年随访（入院日 + 183 天）
  - 一年随访（入院日 + 365 天）
  - 五年随访（入院日 + 1825 天）
- 逾期自动标记与提醒
- 随访详情查看与编辑
- 随访文件上传与下载

### 4. 👥 用户与权限管理

基于自定义 `UserProfile` 模型的三种角色：

| 角色 | 浏览患者 | 新建/修改/删除 | 用户管理 | 知识库管理 | AI 报告生成 |
|------|---------|---------------|---------|-----------|------------|
| 管理员 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 工作人员 | ✅ | ✅ | ❌ | ✅ | ✅ |
| 访客 | ✅（只读） | ❌ | ❌ | ❌ | ✅ |

### 5. 🤖 AI 智能报告生成（*核心新功能*）

#### Ollama 服务管理
- 支持配置多个 Ollama 服务端点
- 模型选择、自定义 System Prompt
- 🧠 **思考模式（Thinking）**：支持带推理链的模型（如 DeepSeek-R1、Qwen3），推理过程与输出分离展示
- 连接测试、启用/禁用切换
- 全局单例管理（同一时间仅一个服务启用）

#### 三种报告生成模式

| 模式 | 说明 | 优势 |
|------|------|------|
| **基础模式** | 直接调用 Ollama 模型生成报告 | 快速、简单 |
| **🆕 RAG 增强模式** | 检索知识库中相似病例和文献作为上下文 | 更专业、有据可依 |
| **🆕 Agent 多步推理** | 分析→检索→草稿→自查→修订 五步流水线 | 最严谨、质量最高 |

#### Agent 五步推理流水线（*最新功能*）

```
Step 1: 分析 (qwen2.5:7b)
  └─ 提取患者关键临床特征、复杂度、风险信号、缺失数据

Step 2: 检索 (ChromaDB 向量库)
  └─ 基于分析结果检索相似病例 + 相关文献

Step 3: 草稿 (qwen2.5:14b, 流式)
  └─ 综合患者数据 + 检索结果生成报告初稿

Step 4: 自查 (qwen2.5:7b)
  └─ 对照原始数据审核初稿（完整性/准确性/安全性/规范性/一致性）

Step 5: 修订 (qwen2.5:14b, 流式)
  └─ 根据审核意见修订定稿
```

- **SSE 流式输出**：所有模式均支持 Server-Sent Events 实时流式传输
- **进度追踪**：Agent 模式显示每步执行状态和中间结果
- **思考过程可视化**：支持 `&lt;think&gt;` 标签解析或 `thinking` 字段，推理与输出分离展示

### 6. 📚 知识库系统（*新功能*）

#### 知识文档管理
- 支持上传 **病例报告**、**文献/论文**、**指南/规范**
- 文件格式：PDF / Word / TXT
- **AI 元数据提取**：自动从文档中提取结构化信息
- 管理员审核后入库

#### 向量化与检索
- **ChromaDB** 向量数据库（默认）
- 多种分块策略：按报告分区 / 按段落 / 固定长度 / 混合
- Embedding 模型：nomic-embed-text（可配置）
- 双集合检索：病例库 + 文献库独立检索
- 结果去重、相似度排序

#### 编辑器反馈学习（*最新功能*）
- 用户编辑 AI 生成报告后，自动计算 diff
- AI 提取编辑摘要
- 支持 **自动学习** / **审核后入库** / **关闭** 三种模式
- 审核通过的报告反馈回知识库，持续提升生成质量

### 7. 📄 报告导出

- **Markdown 实时预览**：编辑器中即时渲染
- **Word (DOCX)** 导出：保留标题层级、加粗/斜体、列表、表格
- **PDF** 导出：基于 ReportLab，支持 CJK 字体

---

## 🏗️ 项目结构

```
Epilepsy-Dataset/
├── epilepsy_portal/                    # 主 Django 项目
│   ├── manage.py                       # Django 命令行入口
│   ├── requirements.txt                # Python 依赖
│   ├── docker-compose.yml              # Docker 部署配置
│   ├── webpack.config.js               # 前端打包配置
│   │
│   ├── epilepsy_portal/                # Django 项目配置
│   │   ├── settings/                   # 多环境配置
│   │   ├── urls.py                     # 根路由
│   │   ├── wsgi.py                     # WSGI 入口
│   │   ├── generic_views.py            # 通用搜索视图
│   │   └── mixins.py                   # 角色权限 Mixin
│   │
│   ├── epilepsy/                       # 核心业务 App
│   │   ├── models.py                   # Patient, FollowUp, OllamaServer 等
│   │   ├── forms.py                    # PatientForm（含完整字段 + 自定义校验）
│   │   ├── views.py                    # 主视图 + AI 报告生成（含 RAG / Agent）
│   │   ├── views_helper.py             # 患者数据导出 JSON 构建
│   │   ├── views_v1.py                 # views.py 的历史版本
│   │   ├── urls.py                     # 业务路由
│   │   ├── admin.py                    # Django Admin 注册
│   │   ├── mixins.py                   # 角色检测工具函数
│   │   ├── json.py                     # 自动生成的字段配置
│   │   └── templatetags/               # 自定义模板标签
│   │
│   ├── knowledge/                      # 🤖 知识库 & AI Agent App（最新）
│   │   ├── models.py                   # KnowledgeSettings, Document, Chunk, EditHistory
│   │   ├── views.py                    # 知识库管理 + 报告编辑保存
│   │   ├── urls.py                     # 知识库路由
│   │   ├── forms.py                    # 知识库表单
│   │   ├── agent_pipeline.py           # Agent 五步推理流水线（SSE 生成器）
│   │   ├── agent_prompts.py            # Agent 四步 Prompt 模板
│   │   ├── rag_prompts.py              # RAG 模式 Prompt
│   │   ├── pipeline.py                 # 文档入库流水线
│   │   ├── retriever.py                # 知识库检索编排
│   │   ├── vector_store.py             # ChromaDB 向量存储封装
│   │   ├── chunker.py                  # 文本分块策略
│   │   ├── embeddings.py               # Embedding 生成
│   │   ├── ollama_client.py            # Ollama API 客户端
│   │   ├── document_processor.py       # 文档文本提取（PDF/Word）
│   │   └── metadata_extractor.py       # AI 元数据提取
│   │
│   ├── api/                            # REST API App
│   │   ├── models.py, views.py, serializers.py, urls.py
│   │   └── migrations/
│   │
│   ├── templates/                      # Django 模板
│   │   ├── epilepsy/                   # 业务页面模板
│   │   │   ├── patient_form.html       # 新建/编辑患者表单
│   │   │   ├── patient_form_partial.html  # 侧滑面板表单片段
│   │   │   ├── patient_list.html       # 患者列表
│   │   │   ├── patient_detail_partial.html  # 🆕 自动生成的详情模板
│   │   │   ├── patient_followup_*.html # 🆕 随访页面
│   │   │   ├── patient_files_panel.html # 🆕 文件管理面板
│   │   │   ├── ollama_form.html        # 🆕 Ollama 服务配置
│   │   │   └── dashboard.html          # 全局总览
│   │   ├── knowledge/                  # 🆕 知识库模板
│   │   │   ├── knowledge_list.html
│   │   │   ├── knowledge_detail.html
│   │   │   ├── upload.html
│   │   │   └── settings.html
│   │   ├── layouts/                    # 页面布局（base, fullscreen）
│   │   ├── includes/                   # 导航、页脚、脚本
│   │   └── registration/              # 登录页
│   │
│   ├── staticfiles/                    # 静态资源
│   │   ├── assets/                     # Argon Dashboard 资源
│   │   └── js/                         # 自定义 JavaScript
│   │
│   ├── generate_patient_detail_partial.py   # 详情模板生成器（旧版）
│   ├── generate_patient_detail_partial_fixed.py  # 详情模板生成器（新版，含图片预览）
│   ├── generate_patient_json.py        # 字段配置生成器
│   └── patched_generate_patient_json.py  # 字段配置生成器（增强版，AST 解析）
│
└── argon-dashboard-django/             # Argon Dashboard 前端参考（不直接运行）
```

---

## 🔧 技术栈

| 层面 | 技术 |
|------|------|
| **Web 框架** | Django 5.2 |
| **前端 UI** | Argon Dashboard (Bootstrap 4) |
| **数据库** | SQLite（开发）/ PostgreSQL（生产推荐） |
| **向量数据库** | 🆕 ChromaDB |
| **AI 推理** | 🆕 Ollama (qwen2.5:7b, qwen2.5:14b, nomic-embed-text 等) |
| **文档导出** | python-docx (Word), ReportLab (PDF) |
| **Markdown** | Python-Markdown (extra, nl2br, tables, fenced_code) |
| **文件处理** | pdfplumber, python-docx (读取) |
| **任务队列** | Celery（可选） |
| **认证** | Django Auth + social-auth (Globus) |
| **API** | Django REST Framework |
| **部署** | Docker, Whitenoise (静态文件) |

---

## 🚀 快速开始

### 环境要求

- Python 3.12+
- Ollama（可选，用于 AI 功能）
- ChromaDB（可选，用于知识库功能）

### 安装

```bash
# 克隆仓库
git clone <repo-url>
cd Epilepsy-Dataset/epilepsy_portal

# 创建虚拟环境
python -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 数据库迁移
python manage.py migrate

# 生成患者字段配置（若 models/templates 有变更）
python patched_generate_patient_json.py
python generate_patient_detail_partial_fixed.py

# 创建超级用户
python manage.py createsuperuser

# 启动开发服务器
python manage.py runserver
```

### 配置 Ollama（AI 功能）

```bash
# 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 拉取所需模型
ollama pull qwen2.5:7b        # 分析 / 自查模型
ollama pull qwen2.5:14b       # 生成 / 修订模型
ollama pull nomic-embed-text  # 向量 Embedding 模型

# 在 Django Admin 或管理设置页中添加 Ollama 服务器
# 地址：http://localhost:11434
```

### Docker 部署

```bash
docker-compose up -d
```

---

## 📖 代码生成器说明

项目中包含两个自动代码生成脚本，确保表单修改后详情页和配置自动同步：

| 脚本 | 功能 |
|------|------|
| `patched_generate_patient_json.py` | 解析 `models.py` 中的 `Patient` 类，自动生成 `PATIENT_GROUP_FIELDS` 和 `FIELDS_FOR_EXPORT` 配置（AST 解析，支持 i18n） |
| `generate_patient_detail_partial_fixed.py` | 解析 `patient_form_partial.html` 中的章节注释和字段引用，自动生成详情展示模板（支持图片预览网格） |

**运行时机**：每次修改 `Patient` 模型字段或表单模板后，重新运行上述脚本。

---

## 🔒 安全与权限

- **视图层**：`RoleRequiredMixin` + `LoginRequiredMixin` 双重保护
- **模板层**：基于 `user.profile.role` 条件渲染操作按钮
- **文件上传**：MD5 + SHA-256 双重哈希校验，防重复存储
- **CSRF**：所有 POST 请求均受 Django CSRF 中间件保护

---

## 📝 开发日志

### 2026-06-06（最新）
- ✅ **Agent 多步推理报告生成**：五步流水线（分析→检索→草稿→自查→修订）
- ✅ **RAG 增强报告生成**：知识库检索 + 向量相似度搜索
- ✅ **知识库系统**：文档上传、AI 元数据提取、分块、ChromaDB 向量化
- ✅ **编辑器反馈学习**：用户编辑报告自动 diff → 摘要 → 入库
- ✅ 思考模式分离：`<think>` 标签解析 + thinking 字段支持
- ✅ Markdown 导出 Word/PDF 完善

### 2026-05（近期）
- ✅ 临床随访提醒功能
- ✅ 患者详情页与编辑页视觉统一
- ✅ 批量操作（下载信息 / 删除 / 下载文件）
- ✅ Ollama 服务管理完善（连接测试、启用控制、思考模式）
- ✅ 文件管理面板（侧滑抽屉）

### 项目初期
- ✅ 核心患者 CRUD
- ✅ 角色权限系统（三级角色）
- ✅ Dashboard 服务器监控
- ✅ Argon Dashboard UI 集成
- ✅ Globus 认证集成
- ✅ 数据导入导出

---

## 📄 许可证

内部项目，用于临床研究用途。
