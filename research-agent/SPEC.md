# SPEC.md — PaperPilot 研究助手

## 1. 问题陈述

科研人员在进行文献调研、论文复现、实验验证时面临碎片化的工作流：搜索论文在 arXiv、读论文在 PDF 阅读器、写代码在 IDE、跑实验在终端。PaperPilot 将这些流程整合为一个智能助手：

- **目标用户**：AI/ML 领域研究者、研究生、需要做文献综述和实验验证的科研人员
- **核心价值**：一个对话界面完成"搜论文→读全文→写代码验证→记录结论"的完整研究闭环

## 2. 用户故事

| ID | 故事 | 验收 |
|------|------|------|
| US1 | 作为研究者，我希望用自然语言搜索最新论文，系统自动摄入知识库 | 输入 topic → 返回论文列表，论文入库可检索 |
| US2 | 作为研究者，我希望阅读论文全文，提取核心方法和贡献 | 调用 read_paper → 返回结构化元数据+全文 |
| US3 | 作为研究者，我希望复现论文声明——写代码、跑实验、对比结果 | file_write → shell_exec → 对比论文数据 |
| US4 | 作为研究者，我希望写文献综述，系统自动搜论文、读全文、输出结构化报告 | 输入 topic → 综述含引用列表 |
| US5 | 作为研究者，我希望跨会话记忆——下次打开项目时看到之前的进度 | 打开项目 → Agent 注入项目笔记到上下文 |
| US6 | 作为研究者，我希望提交长时实验（训练），稍后回来检查结果 | shell_exec(background=true) → check_tasks |

## 3. 功能规约

### 3.1 Agent 核心循环
- **输入**：用户自然语言消息
- **行为**：LLM function calling 决定下一步（retrieve/search_papers/read_paper/shell_exec/...）
- **输出**：流式文本响应 + 工具调用结果
- **边界**：MAX_ROUNDS=5 防无限循环，total_retries=5 防工具失败重试
- **错误处理**：工具失败回传 stderr → LLM 重试或 fallback 生成

### 3.2 工具系统
10 个内置工具 + 可插拔 user tools（`my_tools/` 目录自动导入）：

| 层 | 工具 | 功能 |
|------|------|------|
| 论文 | retrieve | 本地 ChromaDB + BM25 + RRF 混合检索 |
| 论文 | search_papers | arXiv API 搜索→去重→语义切块→入库 |
| 论文 | read_paper | 重建论文全文 + ChromaDB summary metadata |
| 论文 | update_notes | 项目笔记（跨会话持久化） |
| 执行 | shell_exec | Shell 命令（foreground/background） |
| 文件 | file_read/write/edit/glob/grep | 项目工作区文件操作 |
| 管理 | check_tasks | 后台任务状态查询 |

### 3.3 工作区
- 每个项目绑定独立目录（用户可配置）
- 论文摄入时同时写入 `workspace/papers/{id}.md`
- 实验代码和结果保存在 `workspace/experiments/`
- 后台任务日志在 `workspace/tasks/`

### 3.4 上下文引擎
- **系统记忆**：BASE_PROMPT + 工具能力自描述
- **项目记忆**：accumulated_wisdom（跨会话持久）
- **工作记忆**：当前对话 history + 工具调用结果
- **任务注入**：检测 survey/review 关键词 → 注入 SURVEY_WORKFLOW

## 4. 非功能性需求

### 安全
- API Key 通过 WebUI 设置面板输入，存储在浏览器 localStorage
- 桌面模式下可读取环境变量
- **威胁模型**：localStorage 明文风险（浏览器沙箱内可接受）；服务器不存储 key

### 性能
- 检索延迟 < 1s（ChromaDB + BM25）
- arXiv 搜索 < 5s
- 流式响应 token 级到达

### 可用性
- 前端：React + react-markdown + Mermaid
- 桌面：pywebview 原生窗口
- 无外部依赖即可启动（除 API key）

## 5. 系统架构

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│   React UI  │───▶│  FastAPI      │───▶│  Agent Loop     │
│  (Vite)     │◀───│  (SSE Stream) │◀───│  (function call)│
└─────────────┘    └──────────────┘    └─────────────────┘
                                             │
                    ┌────────────────────────┼────────────────────┐
                    │                        │                    │
              ┌─────▼──────┐    ┌───────────▼──┐    ┌──────────▼──┐
              │  ToolRegistry │    │  ChromaDB    │    │  arXiv API  │
              │  (10 tools)  │    │  (vectors)   │    │  (search)   │
              └──────────────┘    └──────────────┘    └─────────────┘
```

## 6. 数据模型

- **Paper**: id, title, authors, year, doi, abstract, source_score
- **Project**: id, topic, workspace_dir, accumulated_wisdom (notes)
- **ConversationTurn**: id, project_id, user_message, assistant_message, compressed, summary
- **AgentState**: user_input, active_project, retrieved_context, read_papers

## 7. 凭据与分发

### 凭据存储
- **WebUI**：localStorage 存储 API config（provider/apiKey/baseUrl/model）
- **Desktop**：支持环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY
- **威胁模型**：localStorage 在同源策略下安全；Desktop 模式下环境变量受 OS 保护

### 分发
- **Web**：`pip install -r requirements.txt && cd frontend && npm install && npm run dev`
- **Desktop**：`python desktop.py`
- **Docker**：`docker compose up`

## 8. 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 后端 | Python + FastAPI | AI 生态成熟，litellm/ChromaDB 原生支持 |
| 前端 | React + TypeScript + Vite | 组件化，react-markdown 渲染学术内容 |
| LLM | DeepSeek (litellm) | 成本低，function calling 支持好 |
| 向量库 | ChromaDB | 轻量嵌入式，无需外部服务 |
| 桌面 | pywebview | 单文件，无 Electron 体积问题 |

## 9. 验收标准

| 功能 | 标准 |
|------|------|
| 论文搜索 | retrieve 返回 ≥1 结果，search_papers 摄入 ≥1 新论文 |
| 综述生成 | read_paper ≥5 次，引用 ≥5 篇，输出 ≥2000 字符 |
| 论文复现 | 包含 file_write + shell_exec 调用链 |
| 长时任务 | background=true 不阻塞，check_tasks 可查状态 |
| 上下文上下文 | 跨会话打开项目时自动注入笔记 |

## 10. V2 迭代（2026-07）

基于 V1 harness 基础，V2 新增：
- **function calling** 替代 JSON 解析（提升工具调用稳定性）
- **ToolRegistry** 可插拔架构（my_tools/ 自动导入）
- **context injection** skill 模式（SURVEY_WORKFLOW 替代硬编码 Python handler）
- **background tasks** + `check_tasks`
- **multi-chat per project** + ChatTabs
- **workspace 侧边栏**（openode 风格可拖拽）
- **语义切块**（TF-IDF 段落相似度）