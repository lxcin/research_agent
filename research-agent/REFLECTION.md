# REFLECTION — Research Agent V1 项目反思

## 一、设计决策回顾

### 1.1 LangGraph → 自实现主循环

**最初选择 LangGraph 的理由**: 快速搭建 agentic loop，条件路由、状态管理、checkpoint 都开箱即用。

**后来替换的原因**:
- 调试困难：`graph.invoke()` 的黑盒行为导致无限循环时无法定位
- 框架依赖：实际上委托了核心循环逻辑，不符合"自己编码 harness"的要求
- 成本不透明：LangGraph 的 checkpoint 机制在 SQLite 中创建了大量隐藏表

**自实现后**: 30 行 while 循环 + 5 个模块，每个组件都可以独立测试，完全透明。

### 1.2 检索驱动 vs 固定管道

**最初**: 固定 router→reasoner→retriever→generator 管道。

**最终**: LLM 自主决定是否检索。LLM 说 retrieve → 执行检索 → 回灌 → LLM 再决策。LLM 说 generate → 直接生成。

**取舍**: 多了 1 次 LLM 调用（retrieve 决策），但灵活性大幅提升。LLM 可以跳过检索直接回答，也可以多轮检索逐步深入。

### 1.3 对话记忆：SQLite + Chroma 双层

**为什么不用纯 Chroma**: Chroma 不支持按时间排序、按 project_id 过滤取最新 N 条。

**为什么不用纯 SQLite**: SQLite 不支持语义搜索。"上次实验数据" → SQLite 找不到"HPLC 纯度分析"。

**双层开销**: 每次对话结束写两次（SQLite INSERT + Chroma 嵌入），但 Chroma 嵌入是本地 ONNX，~50ms，可接受。

### 1.4 Token 预算管理

**为什么不用固定轮数**: 不同模型的上下文窗口不同（4K→128K），固定轮数会浪费或溢出。

**Token 优先级**: system prompt > project info > user input > summaries > recent turns > retrieved context。超出预算时从低优先级开始压缩。

**压缩触发**: 被动懒加载——只在 `build_context` 时检查 token 数，超阈值才触发压缩。不是每轮都做。

## 二、技术债务

### 2.1 中文路由

`router.py` 用空格分词，对中文无效。目前用英文输入绕过。需要引入 jieba 或直接用向量相似度匹配。

### 2.2 Chroma 跨测试持久化

`test_add_and_search` 在 Windows 上偶尔失败，因为 Chroma 的 collection 在测试间未完全隔离。需要给每个测试独立的 collection name。

### 2.3 嵌入模型硬编码

当前使用 Chroma 默认的 `onnx-mini-lm-l6-v2`，不支持中文。中文检索精度会受影响。

### 2.4 模型硬编码

`agent.py` 中 `LiteLLMProvider` 默认 `claude-3-haiku-20240307`。集成测试用 monkey-patch 映射到 `deepseek/deepseek-chat`。应该有配置项。

## 三、如果重来一次

1. **先设计 harness 再写代码** — 第一版直接用了 LangGraph，后来全部重写。如果一开始就确定要自实现主循环，可以省掉 2 次重构。

2. **集成测试要早于单元测试** — 无限循环 Bug 在单元测试中未被发现，因为单元测试只测了单个函数。集成测试在第一天就应该跑起来。

3. **中文 first** — 目标用户是中国科研人员，但路由、分词都假设英文。应该在设计阶段就考虑中文特性。

4. **Git 工作流要更严格** — 初始提交包含了 opencode 内部文件，后续 filter-branch 清理了 2 次才彻底干净。应该用 `.gitignore` 模板 + pre-commit hook。

## 四、Superpowers 使用反思

**有效的部分**:
- brainstorming skill 帮助完成了设计规范
- writing-plans skill 生成了结构化的 15-task 计划
- subagent-driven-development 将独立任务并行分发，效率高

**不够有效的部分**:
- subagent 标注"完成"但实际结果无意义（评估框架），需要人工验证
- subagent 不知道项目的整体上下文，可能做出与设计不一致的实现
- skill 匹配有时过于积极（如将"写报告"匹配到 write-report skill 而非正常的对话）

**改进建议**:
- subagent 的"完成"报告应包含验证步骤（如"运行了评估并得到真实数据"）
- 关键任务（如评估、集成测试）应由人工执行而非委托 subagent

## 五、工程度量

| 指标 | 数值 |
|------|------|
| 总 commits | 35 |
| 模块数 | 15 |
| 单元测试 | 75 |
| 集成测试 | 5 阶段 |
| worktree 数 | 5 |
| 依赖数 | 10（移除了 langgraph/langgraph-checkpoint） |
| 代码行数 | ~3000 |
| 文档行数 | ~3500 |
| 真实 token 消耗 | 907 tokens/次完整对话 |
| 预估成本 | ~$0.0008/次 |