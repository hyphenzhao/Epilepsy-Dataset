# 癫痫术前评估报告 — AI 智能体升级计划

> 创建日期：2026-06-05
> 最后更新：2026-06-06
> 状态：阶段 1 ✅ / 阶段 2 ✅ / 阶段 3 ✅ / 阶段 4 ⏳

---

## 一、现状分析

### 1.1 当前架构

```
患者数据 (JSON) → 构建 prompt → Ollama /api/chat → SSE 流式返回 → 前端渲染 Markdown
```

核心代码路径：

| 函数 | 文件 | 职责 |
|------|------|------|
| `build_patient_report_text()` | `epilepsy/views.py:54` | 将患者数据序列化为 JSON prompt |
| `_build_patient_report_messages()` | `epilepsy/views.py:67` | 组装 system + user 消息 |
| `stream_ollama_report()` | `epilepsy/views.py:110` | 调用 Ollama `/api/chat`，流式解析 |
| `patient_generate_report_stream()` | `epilepsy/views.py:1301` | SSE 视图入口 |
| `build_patient_sections()` | `epilepsy/views_helper.py:272` | 按 12 个分区组织患者字段 |

### 1.2 现有问题

1. **无状态**：每次生成独立，LLM 仅看到当前患者数据 + 一个可选的 system prompt 字符串（`OllamaServer.prompt`）
2. **无学习**：用户修改生成的报告后，修改信息丢失，不会改进后续生成
3. **无知识库**：没有论文、指南、历史病例等背景知识支撑
4. **Prompt 简陋**：仅一句 "请基于以下导出数据生成患者报告。不要编造未提供的信息..."，缺乏领域特异性的指导

### 1.3 关键约束

- **本地部署**，不可使用外部 API
- **Ollama** 为目标推理引擎，目标服务器已安装
- 医疗数据需保持隐私，所有处理在本机完成
- Django + SQLite 技术栈

---

## 二、目标架构

### 2.1 整体设计

```
┌─────────────────────────────────────────────────────────────────┐
│                     报告生成 Agent 架构                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │  病例知识库    │   │  文献知识库    │   │   编辑反馈库      │   │
│  │  (Vector DB)  │   │  (Vector DB)  │   │   (Vector DB)    │   │
│  │              │   │              │   │                  │   │
│  │ · 历史病例报告 │   │ · 论文/指南   │   │ · 用户修改记录    │   │
│  │ · 上传真实报告 │   │ · 教科书摘录  │   │ · 人工审校版本    │   │
│  │ · 模板报告    │   │ · 科室规范    │   │ · 编辑 diff      │   │
│  └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘   │
│         │                  │                     │              │
│         └────────┬─────────┴──────────┬──────────┘              │
│                  │                    │                         │
│          ┌───────▼────────────────────▼──────────┐              │
│          │        混合检索引擎                      │              │
│          │  (向量相似度 + BM25 关键词 + 分区匹配)    │              │
│          └────────────────┬──────────────────────┘              │
│                           │                                    │
│          ┌────────────────▼──────────────────────┐              │
│          │         Prompt 组装 & 多步推理          │              │
│          │                                       │              │
│          │  Step 1: 分析关键特征                   │              │
│          │  Step 2: 检索知识库                     │              │
│          │  Step 3: 生成草稿                       │              │
│          │  Step 4: 自查审核                       │              │
│          │  Step 5: 修订定稿                       │              │
│          └────────────────┬──────────────────────┘              │
│                           │                                    │
│          ┌────────────────▼──────────────────────┐              │
│          │       Ollama 本地模型推理               │              │
│          │   (qwen2.5 / deepseek-r1 / ...)       │              │
│          └───────────────────────────────────────┘              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
用户点击"生成报告"
  │
  ├─ 1. 特征提取（轻量模型）
  │    └─ 从患者数据中提取关键特征：发作类型、侧向性、影像异常、EEG 模式等
  │
  ├─ 2. 并行检索
  │    ├─ 病例知识库：搜索相似患者 → top-K 相似病例的完整报告
  │    ├─ 文献知识库：搜索相关指南/论文 → top-M 相关片段
  │    └─ 编辑反馈库：搜索相关修改记录 → 修正建议
  │
  ├─ 3. 增强 Prompt 组装
  │    ├─ System: 领域角色 + 输出规范 + 写作风格
  │    ├─ Context: 检索到的 top-K 病例 + top-M 文献 + 修正建议
  │    └─ User: 当前患者完整数据
  │
  ├─ 4. 流式生成（主力模型）
  │    └─ Ollama streaming → SSE → 前端 Markdown 渲染
  │
  └─ 5. 生成后
       ├─ 前端展示报告，用户可编辑
       ├─ 编辑后保存 → diff 计算 → 编辑反馈库待审核
       └─ 管理员审核 → 确认有价值 → 纳入病例知识库
```

---

## 三、分阶段实施计划

### 阶段 1：知识库基础设施（P0）

**目标**：搭建向量存储和文档管理的基础能力

#### 新增 Django Models

```python
class KnowledgeDocument(models.Model):
    """知识库文档（统一存储病例报告、论文、指南等）"""
    DOC_TYPE_CHOICES = [
        ("case_report", "病例报告"),
        ("literature", "文献/论文"),
        ("guideline", "指南/规范"),
        ("edited_report", "用户修改的报告"),
    ]
    title = models.CharField(max_length=500)
    doc_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)
    source_patient = models.ForeignKey("Patient", null=True, blank=True, 
        on_delete=models.SET_NULL, help_text="关联患者（病例报告类）")
    original_file = models.FileField(upload_to="knowledge/raw/", blank=True,
        help_text="原始上传文件（PDF/Word/文本）")
    text_content = models.TextField(help_text="提取/清洗后的纯文本")
    metadata = models.JSONField(default=dict, 
        help_text="提取的结构化信息，如发作类型、EEG发现等")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    is_approved = models.BooleanField(default=False, 
        help_text="管理员审核后入库")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class KnowledgeChunk(models.Model):
    """文档分块，向量化的基本单位"""
    document = models.ForeignKey(KnowledgeDocument, 
        related_name="chunks", on_delete=models.CASCADE)
    chunk_index = models.IntegerField()
    text = models.TextField()
    embedding_id = models.CharField(max_length=100, 
        help_text="向量库中的 ID")
    section_label = models.CharField(max_length=200, blank=True,
        help_text="对应报告分区，如'发作症状学'")
    token_count = models.IntegerField(default=0)


class ReportEditHistory(models.Model):
    """用户对生成报告的编辑历史"""
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE)
    original_markdown = models.TextField(help_text="AI 原始生成的 Markdown")
    edited_markdown = models.TextField(help_text="用户修改后的 Markdown")
    diff_json = models.JSONField(default=dict, 
        help_text="结构化的修改记录")
    edit_summary = models.TextField(blank=True,
        help_text="AI 提取的修改摘要")
    is_accepted = models.BooleanField(default=False,
        help_text="管理员审核后同意入库")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

#### 向量数据库选型：ChromaDB

- **零配置**：Python 原生，pip install 即可
- **存储**：SQLite 后端（与 Django 一致）
- **API 简洁**：`collection.add()`, `collection.query()`, `collection.get()`
- **支持自定义 embedding function**：可直接对接 Ollama embedding API
- **轻量**：无额外服务进程

#### 任务清单

- [ ] 创建 `knowledge` Django app 及上述 models
- [ ] 安装 ChromaDB (`pip install chromadb`)
- [ ] 实现 Ollama embedding 封装（调用 `/api/embeddings`，模型：`nomic-embed-text`）
- [ ] 实现文档上传 → 文本提取（PDF/Word → plain text）
- [ ] 实现智能分块（按 12 个报告分区做语义分块）
- [ ] 实现向量化入库流水线
- [ ] Django Admin 知识库管理界面

**工作量估算**：3-5 天

---

### 阶段 2：文档处理流水线（P0）

**目标**：上传 → 提取 → 分块 → 向量化的完整自动化流水线

#### 2a. 关键信息提取

使用 Ollama (`qwen2.5:7b`) 从上传文档中提取结构化信息：

- **病例报告** → 提取：
  - 患者基本情况（年龄、性别）
  - 发作类型与症状学
  - EEG 关键发现（发作间期/发作期放电模式、侧向性、定位）
  - MRI 异常发现
  - SEEG 结果（如有）
  - 手术方式与范围
  - 预后（Engel 分级等）
  - 特殊/罕见发现

- **论文/指南** → 提取：
  - 核心论点
  - 适用场景
  - 关键证据等级
  - 推荐意见

通过精心设计的 JSON Schema prompt 实现结构化提取，存入 `KnowledgeDocument.metadata`。

#### 2b. 智能分块策略

利用癫痫报告天然的结构化特征（12 个分区）：

```
分块策略：
├─ 按 section 分块（推荐粒度）
│   ├─ Chunk 1: 基本信息
│   ├─ Chunk 2: 病史
│   ├─ Chunk 3: 发作症状学
│   ├─ ... 
│   └─ Chunk 12: 评估信息
│
├─ section 内按需细分（如果单个 section 内容过长）
│   └─ 以自然段落为边界，保持语义完整性
│
└─ 重叠窗口（overlap = 1-2 句）避免边界信息丢失
```

#### 2c. 用户编辑反馈闭环

```
用户在前端编辑生成的报告
  │
  ├─ 保存编辑 → ReportEditHistory 创建
  │
  ├─ diff 分析（python difflib）
  │   ├─ 识别新增内容
  │   ├─ 识别删除内容
  │   └─ 识别修改内容
  │
  ├─ AI 提取修改摘要
  │   └─ "用户在'发作症状学'部分补充了自动症的具体描述"
  │   └─ "用户将'建议手术'修改为'建议进一步 SEEG 评估'"
  │
  └─ 管理员审核
      ├─ 通过 → 转为 KnowledgeDocument(doc_type="edited_report") → 向量化入库
      └─ 不通过 → 保留记录但不出库
```

#### 任务清单

- [ ] 实现关键信息提取 prompt 模板（病例报告 / 论文两种）
- [ ] 实现 JSON 解析与 `metadata` 存储
- [ ] 实现智能分块器（section-aware chunker）
- [ ] 前端上传界面（拖拽上传 PDF/Word）
- [ ] 前端编辑 diff 收集与保存
- [ ] 管理员审核队列页面
- [ ] 测试流水线端到端

**工作量估算**：2-3 天

---

### 阶段 3：RAG 增强报告生成（P0/P1）

**目标**：将知识库检索结果注入报告生成 prompt，大幅提升报告质量

#### 3a. 混合检索引擎

```
查询构建：
  当前患者关键特征 → 检索查询（向量化）

检索策略（混合）：
  ├─ 向量相似度检索（语义相似）
  │   └─ ChromaDB query (cosine similarity)
  │
  ├─ BM25 关键词检索（精确匹配）
  │   └─ rank_bm25 库或自实现
  │
  └─ 混合排序（Reciprocal Rank Fusion）
      └─ score = α × vector_score + (1-α) × bm25_score
```

#### 3b. 新的 Prompt 结构

```python
def build_enhanced_report_prompt(patient, knowledge_context):
    return {
        "system": """
你是一位资深的癫痫术前评估报告撰写专家。

## 写作要求
- 使用专业、准确的中文医学语言
- 报告结构遵循标准癫痫术前评估报告格式
- 基于提供的患者数据撰写，不要编造
- 如果某项数据缺失，明确标注"未提供"
- 参考提供的相似病例和指南作为写作风格和内容参考

## 报告结构
1. 基本信息
2. 病史摘要
3. 发作症状学
4. 辅助检查结果（EEG、MRI、PET）
5. 无创评估结论
6. 有创评估结论（如有 SEEG）
7. 综合印象与建议
""",
        "context": f"""
## 相似病例参考（共 {len(knowledge_context.cases)} 个）
{knowledge_context.format_cases()}

## 相关指南/文献摘录
{knowledge_context.format_literature()}

## 历史编辑修正经验
{knowledge_context.format_edits()}
""",
        "user": build_patient_report_text(patient),
    }
```

#### 3c. 分步检索策略

对于结构化的癫痫报告，不一定要全文检索，可以在每个 section 范围内做定向检索：

```
生成"发作症状学"部分时 →
  只检索历史报告中"发作症状学"分区的 chunks →
  获得该 section 的写作风格和术语参考
```

#### 任务清单

- [ ] 实现 BM25 关键词检索
- [ ] 实现混合检索（RRF 融合排序）
- [ ] 实现 `build_enhanced_report_prompt()` 
- [ ] 修改 `stream_ollama_report()` 支持知识上下文注入
- [ ] 实现分 section 定向检索
- [ ] 前端适配新的 SSE 事件流（可能需要额外传递引用信息）
- [ ] A/B 对比测试（有 RAG vs 无 RAG）

**工作量估算**：2-3 天

---

### 阶段 4：Agent 多步推理（P2）

**目标**：从简单的 prompt→response 升级为多步推理链

#### 推理链设计

```
Step 1: 分析 (Analysis)
  模型: qwen2.5:7b
  输入: 患者完整数据
  输出: {
    "key_features": ["左侧颞叶癫痫", "海马硬化", "MRI 阳性"],
    "complexity": "moderate",
    "missing_data": ["神经心理评估未完成"],
    "suggested_diagnosis": "左侧颞叶内侧癫痫"
  }

Step 2: 检索 (Retrieval)
  输入: Step 1 的 key_features
  输出: top-K 相似病例 + top-M 文献片段

Step 3: 草稿 (Draft)
  模型: qwen2.5:14b 或 deepseek-r1:14b
  输入: 患者数据 + 检索上下文 + 写作指令
  输出: 完整报告草稿 (Markdown)

Step 4: 自查 (Self-Critique)
  模型: qwen2.5:7b
  输入: 草稿 + 患者原始数据 + 指南规范
  输出: {
    "issues": [
      {"section": "发作症状学", "problem": "未提及发作频率", "severity": "medium"},
      {"section": "影像学", "problem": "未说明海马体积比较", "severity": "low"}
    ],
    "overall_score": 7.5,
    "revision_needed": true
  }

Step 5: 修订 (Revise)
  模型: qwen2.5:14b
  输入: 草稿 + 自查问题列表
  输出: 修订后的最终报告
```

#### 推理链的状态管理

```python
class ReportGenerationPipeline:
    """管理多步推理的状态"""
    
    def __init__(self, patient, server):
        self.patient = patient
        self.server = server
        self.state = {
            "step": "init",
            "analysis": None,
            "retrieved_context": None,
            "draft": None,
            "critique": None,
            "final": None,
        }
    
    async def execute(self):
        # Step 1: 分析
        self.state["analysis"] = await self.analyze()
        yield self.format_event("progress", "分析患者关键特征...")
        
        # Step 2: 检索
        self.state["retrieved_context"] = await self.retrieve()
        yield self.format_event("progress", f"检索到 {len(self.state['retrieved_context'])} 条参考信息")
        
        # Step 3: 草稿
        self.state["draft"] = await self.draft()
        yield self.format_event("progress", "草稿完成，开始自查...")
        
        # Step 4: 自查
        self.state["critique"] = await self.critique()
        yield self.format_event("progress", f"自查发现 {len(self.state['critique']['issues'])} 个问题")
        
        # Step 5: 修订
        async for chunk in self.revise():
            yield chunk  # 流式输出最终报告
```

#### 任务清单

- [ ] 设计并实现 `ReportGenerationPipeline`
- [ ] 实现每个 Step 的 prompt 模板和调用逻辑
- [ ] 前端适配多步进度展示（显示当前步骤和进度信息）
- [ ] 实现 pipeline 可中断/可恢复
- [ ] Dashboard 展示推理详情（调试用）

**工作量估算**：3-5 天

---

## 四、Ollama 模型推荐

| 用途 | 推荐模型 | 大小 | 内存需求 | 理由 |
|------|---------|------|---------|------|
| **Embedding** | `nomic-embed-text` | ~137M | ~300MB | 轻量高效，中文支持可接受，Ollama 原生 |
| | `bge-m3` (备选) | ~570M | ~1GB | 多语言效果更好，1024 维 |
| **关键信息提取** | `qwen2.5:7b` | ~4.7GB | ~6GB | 中文医学场景优秀，结构化 JSON 输出可靠 |
| **报告生成（主力）** | `qwen2.5:14b` | ~8.9GB | ~12GB | 中文流畅、推理深度好 |
| | `deepseek-r1:14b` (备选) | ~8.9GB | ~12GB | 思考链对复杂医学决策特别有用 |
| **自查/审核** | `qwen2.5:7b` | ~4.7GB | ~6GB | 速度快，适合快速校验 |
| **报告生成（轻量）** | `qwen2.5:7b` | ~4.7GB | ~6GB | 8GB 显存环境的主力选择 |

### 目标部署环境

| 项目 | 参数 |
|------|------|
| GPU 显存 | **20 GB** |
| Embedding | `nomic-embed-text`（~300MB） |
| 主力生成 | `qwen2.5:14b`（~12GB）或 `deepseek-r1:14b`（~12GB） |
| 提取/自查 | `qwen2.5:7b`（~6GB） |

> **20GB 定位**：介于中配和高配之间。可以同时加载 embedding 模型 + 一个主力模型（14b 约 12GB），剩余约 7GB 余量。14b + 7b 不可同时常驻（~18GB），但 Ollama 的 LRU 自动卸载机制可以在空闲时切换模型，实际使用无感知。如需极致性能可尝试 `qwen2.5:32b`（~19GB），但几乎没有余量，不推荐。

### 推荐模型组合（20GB 最佳实践）

```
常驻模型 (始终加载):
  nomic-embed-text  ......... ~0.3 GB  (向量检索)

主力模型 (按需切换，Ollama 自动管理):
  qwen2.5:14b  ............. ~12  GB  (报告生成 / 复杂推理)
  qwen2.5:7b  .............. ~6   GB  (信息提取 / 自查审核)
  deepseek-r1:14b  ......... ~12  GB  (备选，思考链场景)

峰值显存使用:
  nomic-embed-text + qwen2.5:14b = ~12.3 GB  ✅ 充裕
  nomic-embed-text + qwen2.5:7b  = ~6.3  GB  ✅ 充裕
  两个 14b 级模型同时加载          = ~24  GB  ❌ 不可行 (Ollama LRU 自动规避)
```

### 针对不同硬件配置的建议（参考）

<details>
<summary>低配（8GB VRAM）</summary>

- Embedding: `nomic-embed-text`
- 全部任务共用 `qwen2.5:7b`（生成 + 提取 + 自查）
- 关键：精心设计的 prompt + RAG 可以弥补模型大小不足
</details>

<details>
<summary>中配（16GB VRAM）</summary>

- Embedding: `nomic-embed-text`
- 生成: `qwen2.5:14b` 或 `deepseek-r1:14b`
- 提取/自查: `qwen2.5:7b`
- 两个模型可同时加载
</details>

<details>
<summary>高配（24GB+ VRAM）</summary>

- Embedding: `bge-m3`
- 生成: `deepseek-r1:14b` 或 `qwen2.5:32b`
- 提取/自查: `qwen2.5:14b`
- 最佳效果，最适合 Agent 化后的多模型协作
</details>

---

## 五、技术细节与注意事项

### 5.1 文档格式支持

| 格式 | 提取方式 | 依赖 |
|------|---------|------|
| `.txt` / `.md` | 直接读取 | 无 |
| `.pdf` | pdfplumber / PyMuPDF | `pip install pdfplumber` |
| `.docx` | python-docx | 项目已使用 |
| `.html` | BeautifulSoup | `pip install beautifulsoup4` |

### 5.2 分块大小建议

```
ChromaDB 建议 chunk size: 500-1000 tokens
癫痫报告一个 section 通常: 200-800 tokens
→ 按 section 分块天然匹配推荐粒度
→ 对于特别长的 section（如"发作症状学"有的会很详细），可按段落再细分
```

### 5.3 检索参数调优

```
向量检索 top-K:
  - 病例检索: k=3~5 个最相似病例
  - 文献检索: k=5~10 个最相关片段
  - 编辑反馈: k=3 条最相关修正

混合检索权重:
  - 初始建议 α=0.7（偏向量），后续根据实际效果调优
```

### 5.4 隐私与安全

- 所有数据存储在本机，不访问外部 API
- 知识库内容可通过 Django 权限系统控制访问
- 建议对导出/下载的知识库内容添加水印或审计日志
- 患者关联的病例报告应遵循与患者数据相同的权限模型

### 5.5 向后兼容

- 保留现有 `OllamaServer.prompt` 字段，将其作为"全局系统指令"注入
- 保留现有 SSE 事件格式，新增字段而非替代
- 新增功能通过 feature flag 或独立的"增强生成"按钮暴露
- 提供"传统模式"切换，用户可选择不使用知识库增强

---

## 六、前端 UI 变更概要

### 6.1 知识库管理页面

```
/kn owledge/              → 知识库列表（支持搜索、筛选）
/knowledge/upload/        → 上传文档（拖拽 + 类型选择）
/knowledge/<id>/          → 文档详情（原文 + 提取的元数据）
/knowledge/<id>/chunks/   → 分块预览
/knowledge/review/        → 管理员审核队列（编辑反馈审核）
```

### 6.2 报告生成增强

```
患者详情页 →
  [生成报告] 按钮 → 弹出选项
    ├─ 传统模式（不检索知识库）
    └─ 增强模式（检索知识库增强） ← 默认
        ├─ 显示检索到的参考信息
        ├─ 流式生成中显示当前步骤
        └─ 完成后显示参考来源
```

### 6.3 编辑与反馈

```
报告展示区 →
  [编辑] 按钮 → Markdown 编辑器
  [保存修改] → 自动计算 diff → 提交审核
  [导出 Word/PDF] → 保持现有功能
```

---

## 七、里程碑与时间线

| 里程碑 | 内容 | 预计完成 |
|--------|------|---------|
| M1 | 阶段 1 完成：知识库基础设施可用，可上传和向量化文档 | 第 1 周 |
| M2 | 阶段 2 完成：流水线完整，编辑反馈闭环跑通 | 第 2 周 |
| M3 | 阶段 3 完成：RAG 增强生成上线，报告质量显著提升 | 第 3 周 |
| M4 | 阶段 4 完成：Agent 多步推理，完整智能体体验 | 第 4-5 周 |

---

## 八、用户可配置项

以下设计决策不预设固定值，改为在系统设置中提供选项，由用户根据实际需求自行配置：

### 8.1 设置项设计

```python
# 新增 KnowledgeSettings 模型或扩展 OllamaServer
class KnowledgeSettings(models.Model):
    # 向量数据库后端
    VECTOR_BACKEND_CHOICES = [
        ("chromadb", "ChromaDB（推荐，功能完善）"),
        ("sqlite_vec", "SQLite-vec（零额外依赖）"),
    ]
    vector_backend = models.CharField(
        max_length=20, choices=VECTOR_BACKEND_CHOICES, default="chromadb",
        verbose_name="向量数据库后端"
    )

    # 分块策略
    CHUNK_STRATEGY_CHOICES = [
        ("section", "按报告分区（推荐）"),
        ("paragraph", "按段落"),
        ("fixed", "固定长度（500 tokens）"),
        ("hybrid", "混合（分区 + 段落细分）"),
    ]
    chunk_strategy = models.CharField(
        max_length=20, choices=CHUNK_STRATEGY_CHOICES, default="section",
        verbose_name="文档分块策略"
    )

    # 知识库共享范围
    SCOPE_CHOICES = [
        ("global", "全科室共享"),
        ("role", "按角色隔离（管理员/医生/访客）"),
        ("user", "按用户隔离"),
    ]
    knowledge_scope = models.CharField(
        max_length=20, choices=SCOPE_CHOICES, default="global",
        verbose_name="知识库共享范围"
    )

    # 编辑学习策略
    LEARNING_MODE_CHOICES = [
        ("review", "管理员审核后入库（推荐）"),
        ("auto", "自动学习"),
        ("disabled", "关闭编辑学习"),
    ]
    learning_mode = models.CharField(
        max_length=20, choices=LEARNING_MODE_CHOICES, default="review",
        verbose_name="编辑反馈学习策略"
    )
```

### 8.2 前端设置页面

在现有的 Ollama 设置页面旁边增加"知识库设置"Tab：

```
设置页面
├── Ollama 服务器设置（已有）
├── 用户管理（已有）
└── 知识库设置（新增）
    ├── 向量数据库：  [ChromaDB ▼]  ⓘ 切换后需重建索引
    ├── 分块策略：    [按报告分区 ▼]  ⓘ 新上传的文档将使用此策略
    ├── 共享范围：    [全科室共享 ▼]  ⓘ 控制知识库的可见范围
    ├── 编辑学习：    [审核后入库 ▼]  ⓘ 用户修改报告后的学习方式
    └── 检索设置：
        ├── 相似病例数 (top-K)：[3 ▬▬▬○ 10] 默认 5
        ├── 文献片段数 (top-M)：[5 ▬▬▬○ 20] 默认 10
        └── 混合检索权重 (α)：  [0.5 ▬▬▬○ 1.0] 默认 0.7
```

### 8.3 硬件配置

✅ 已确认 — **20 GB** VRAM。模型推荐：`qwen2.5:14b`（主力）+ `nomic-embed-text`（向量）+ `qwen2.5:7b`（提取/自查）。

---

## 九、实施进度日志

### 2026-06-05 — 阶段 1 完成 ✅

**新建 `knowledge/` Django app（17 个文件）：**

| 文件 | 说明 |
|------|------|
| `knowledge/__init__.py` | |
| `knowledge/apps.py` | App 注册，verbose_name="知识库" |
| `knowledge/models.py` | 4 个 Model：`KnowledgeSettings`, `KnowledgeDocument`, `KnowledgeChunk`, `ReportEditHistory` |
| `knowledge/forms.py` | `KnowledgeSettingsForm`, `KnowledgeUploadForm` |
| `knowledge/admin.py` | 4 个 Admin 注册，含批量审核/采纳操作 |
| `knowledge/views.py` | 6 个视图（settings, list, detail, upload, upload_preview, save_report_edit, delete） |
| `knowledge/urls.py` | 7 条路由 |
| `knowledge/embeddings.py` | Ollama `/api/embeddings` 封装（nomic-embed-text, 768 维） |
| `knowledge/vector_store.py` | ChromaDB 封装（cases + literature 两个 collection） |
| `knowledge/chunker.py` | 4 种分块策略（section/paragraph/fixed/hybrid） |
| `knowledge/document_processor.py` | PDF/Word/TXT → 纯文本提取 |
| `knowledge/pipeline.py` | 向量化入库流水线（ingest_document, ingest_markdown_report, delete_document, rebuild_index） |
| `knowledge/ollama_client.py` | Ollama `/api/chat` 同步调用封装 |
| `knowledge/metadata_extractor.py` | AI 元数据提取（病例 9 字段 / 文献 4 字段） |
| `knowledge/migrations/0001_initial.py` | 初始 migration |
| `knowledge/migrations/0002_add_extraction_prompt.py` | 新增 metadata_extraction_prompt 字段 |

**新建模板（5 个）：**

| 文件 | 说明 |
|------|------|
| `templates/knowledge/settings.html` | 知识库设置页面（后端 + 分块 + 策略 + 检索参数） |
| `templates/knowledge/knowledge_list.html` | 文档列表（类型筛选 + 搜索 + 分页） |
| `templates/knowledge/knowledge_detail.html` | 文档详情（内容 + 元数据 + 分块表） |
| `templates/knowledge/upload.html` | 上传页面（拖拽 + 元数据预览 + 确认入库） |
| `templates/knowledge/forbidden.html` | 403 权限拒绝页 |

**新建静态文件（1 个）：**

| 文件 | 说明 |
|------|------|
| `static/knowledge/js/upload.js` | 上传页 JS（drag-drop + 动态元数据表单） |

**修改已有文件：**

| 文件 | 变更 |
|------|------|
| `epilepsy_portal/settings/base.py` | 注册 `knowledge` app |
| `epilepsy_portal/urls.py` | 添加 `knowledge/` URL 前缀 |
| `epilepsy/views.py` | `UserListView.get_context_data` 传入 `knowledge_settings` |
| `templates/epilepsy/user_list.html` | 新增知识库设置卡片 |
| `templates/epilepsy/patient_list.html` | 报告模态框新增编辑按钮 + Markdown 编辑器 + 保存逻辑 |
| `templates/includes/sidenav.html` | 新增"知识库"和"上传文档"导航链接 |

**已安装 Python 依赖：**
- `chromadb` 1.5.9
- `pdfplumber`

---

### 2026-06-05 — 阶段 2 完成 ✅

**核心流程就绪：**

1. **文档上传流水线**：拖拽文件 → 文本提取 → AI 元数据提取 → 预览/编辑 → 确认 → 分块 → ChromaDB 向量化 → 列表可查
2. **报告编辑反馈**：生成报告 → 点击"修改报告" → Markdown 编辑 → 保存 → diff + AI 摘要 → `ReportEditHistory` → 管理员审核/自动学习
3. **知识库浏览**：列表页支持类型筛选 + 全文搜索 + 分页，详情页展示内容/元数据/分块

**需要测试验证的事项：**

- [ ] Ollama 服务可用（需安装 `qwen2.5:7b` + `nomic-embed-text` 模型）
- [ ] 元数据提取端到端（上传真实 PDF/Word 病例报告）
- [ ] ChromaDB collection 创建和数据写入
- [ ] 上传页面完整流程（上传 → 预览 → 确认入库）
- [ ] 报告编辑 → 保存 → ReportEditHistory 记录
- [ ] 编辑自动学习模式（KnowledgeSettings.learning_mode = "auto"）

**已知已修复：**
- ✅ embedding/chat 模型从硬编码改为 KnowledgeSettings 可配置
- ✅ `knowledge/settings.html` 已含 Ollama 模型配置 + 元数据提取 Prompt 字段
- ✅ Ollama 模型已 pull（nomic-embed-text + qwen2.5:7b）

---

### 2026-06-06 — 阶段 3 完成 ✅

**新增 2 个文件：**

| 文件 | 用途 |
|------|------|
| `knowledge/retriever.py` | 检索编排：build_query_text → 并行检索病例+文献 → 去重 → format_context_for_prompt |
| `knowledge/rag_prompts.py` | RAG System Prompt（专家角色 + 写作规范） |

**修改 6 个文件：**

| 文件 | 变更 |
|------|------|
| `epilepsy/views.py` | 新增 `_build_rag_patient_report_messages()`, `_build_rag_system_prompt()`, `patient_generate_report_stream_rag()`；`stream_ollama_report()` 支持 `messages`/`pre_events` 参数 |
| `epilepsy/urls.py` | 新增 `generate-report-rag/` 路由 |
| `knowledge/models.py` | `KnowledgeSettings` 新增 `enable_rag` 字段 |
| `knowledge/forms.py` + `templates/knowledge/settings.html` | RAG 开关 UI |
| `templates/epilepsy/patient_list.html` | 报告模态框 🧠 RAG 增强复选框 + retrieval SSE 事件 |

**用户操作：** 模态框默认勾选 RAG 增强 → 生成时自动检索知识库 → Thinking 面板显示检索统计 → 流式生成（注入参考上下文）→ 取消勾选恢复传统模式。


---

## 十、待完成

### 阶段 4：Agent 多步推理（P2，工作量 3-5 天）

将当前单次 prompt→response 升级为多步推理链：

```
Step 1: 分析 → 提取患者关键特征（qwen2.5:7b）
Step 2: 检索 → 根据特征检索知识库（retriever.py ✅）
Step 3: 草稿 → 主力模型生成报告初稿（qwen2.5:14b）
Step 4: 自查 → 检查草稿遗漏/与指南冲突（qwen2.5:7b）
Step 5: 修订 → 根据自查意见修改定稿（qwen2.5:14b）
```

**前置条件**：知识库中有足够的参考文档，阶段 3 RAG 基础链路已验证可用。

---

*本文档将随实施进展持续更新。*
