# 当前模块导览

本页以当前源码为准；ARCHITECTURE.md 中的部分内容为历史设计。

| 部分 | 文件 | 职责 |
| --- | --- | --- |
| 交互入口 | cli.py、server.py | 命令行与 HTTP/SSE |
| 应用装配 | agent.py | 项目绑定、插件装配、回合后处理 |
| 执行内核 | runtime.py | 模型调用、工具分发、循环收敛 |
| 基础契约 | models.py、config.py、llm.py | 状态、配置、模型适配 |
| 上下文 | context.py、brief.py、attachments.py | 上下文预算、简报、附件 |
| 工具插件 | tools/schema.py、tools/__init__.py、tools/builtin/ | 工具契约、注册和内置能力 |
| 扩展接入 | tools/mcp_loader.py、my_tools/ | MCP 与用户工具 |
| 子代理 | tools/subagent.py | 子任务派发 |
| 任务记忆 | plan.py、plan_pipeline.py、embedding.py | 计划存储、异步提炼、检索 |
| 计划调度 | plan_executor.py、tools/builtin/plan.py | 依赖调度及执行工具 |
| 会话存储 | memory/__init__.py、project_manager.py | 对话和项目持久化 |
| 执行治理 | guardrail.py、sandbox.py、validate.py | 命令检查、隔离、写入校验 |
| 改动管理 | checkpoint.py、proposal.py、tools/git_tool.py | 快照、审阅、恢复 |
| 可观测性 | telemetry.py、trace_log.py、diagnostics/ | 用量、事件、故障与审计 |
| 质量门禁 | quality/ | 检查收集和报告 |
| 经验技能 | report.py、evolve.py、skill_loader.py | 经验沉淀及技能加载 |

推荐按「入口 → agent 装配 → runtime 契约 → 工具注册 → 具体插件」阅读，再研究计划、治理和诊断。

整理边界：入口负责交互，内核负责循环，插件负责具体行为。计划调度通过 dispatch 回调连接执行能力；回合后记忆处理留在 pipeline。移动模块前检查调用点和测试，保留公开导入路径。旧 Tier B 记忆模块已移除，不应按历史文档恢复其依赖。
