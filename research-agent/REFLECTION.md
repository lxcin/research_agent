# REFLECTION — PaperPilot 项目反思

## 一、V1 设计决策回顾

### 1.1 LangGraph → 自实现主循环
最初用 LangGraph 快速搭建，调试困难（graph.invoke() 黑盒导致无限循环），改为 30 行 while 循环 + 独立模块，完全透明可测试。

### 1.2 检索驱动 vs 固定管道
最初固定 router→retriever→generator 管道。最终 LLM 自主决定是否检索，通过 function calling 实现。

### 1.3 上下文架构的三次迭代
- V1: 简单 token 计数截断（4000 tokens 硬编码）
- V2 early: `trim_messages` 一刀切 + 两个 build 函数（职责不清）
- V2 final: 按模型自适应 token 上限 + 分层注入（系统→项目→历史→工具结果→用户输入）

## 二、V2 关键洞察

### 2.1 Function Calling > JSON 解析
V1 让 LLM 输出 `{"action": "retrieve"}` JSON，但 DeepSeek 有时输出 markdown fence。改用 litellm native function calling 后稳定性大幅提升。

### 2.2 Skill ≠ Python Handler
最初把文献综述写成 Python handler，LLM 不愿调用且失去流程控制。改为 context injection——注入 SURVEY_WORKFLOW 到 prompt，LLM 用自己的工具执行。这才是 skill 的正确形态。

### 2.3 论文全文为什么消失
调查发现 `build_chat_context` 根本不包含 tool results，造成"读了 13 篇只引用 4 篇"。现代 LLM 支持 64K+ 上下文，不应该截断工具结果。修复后 17 read 17 cited。

### 2.4 Prompt 分段的重要性
`=== 以下是工具调用结果 ===` 分隔线让 LLM 知道"这是数据，这是任务"。不加分隔线时 DeepSeek 输出 `<tool_calls>` XML。分段后正常生成。

## 三、哪些 Superpowers 技能发挥了最大作用

| 技能 | 作用 | 评分 |
|------|------|------|
| brainstorming | 追问边界条件，迫使在设计阶段想清楚 | ★★★★★ |
| writing-plans | 15 task 分解清晰，验证步骤明确 | ★★★★ |
| test-driven-development | 写失败测试 → 让代码通过 | ★★★★ |
| subagent-driven-development | 15 个 task 并行推进 | ★★★★ |
| using-git-worktrees | worktree 隔离并行 task | ★★★ |

**形式大于实质的**：V1 的评估（T7）标注 done 但结果无意义——subagent 说"完成"不等于真正完成。

## 四、Prompt / Context 策略

最有效的策略：
1. **把任务要求放在最后**——LLM 对最后的 system message 关注度最高
2. **用分隔线标记章节**——`===` 是强视觉信号
3. **不限制输出长度**——让 LLM 写到满意，不要截断

## 五、凭据与分发

LocalStorage 明文存储 API key 在浏览器沙箱内可接受。Desktop 模式用环境变量。关键安全措施：`.gitignore` 排除 `.env`，CI 检查硬编码凭据。

## 六、如果重做会改变什么

1. 先写 context injection 工作流再做工具
2. 不写死 token 限制，按模型自适应
3. 第一个 subagent task 就加入端到端测试，不等 T14

## 七、对 Superpowers 的批判

Superpowers 假设"subagent 的完成就是完成"——这在 AI 生成代码的场景下不成立。V1 的评估框架（T7）subagent 标注 done，但实际结果无意义。需要人工验证门禁。

TDD 在 AI 协作下是放大器：它让 subagent 知道"我的代码必须通过这些测试"，减少了瞎编代码。但没有 mock LLM 时测试依赖真实 API，CI 中无法运行。