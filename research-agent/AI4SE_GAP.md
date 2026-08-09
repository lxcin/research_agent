# AI4SE 缺口分析与执行计划

## 已有 vs 要求对照

| 要求 | 现有 | 缺口 |
|------|------|------|
| Agent 主循环 | agent.py (600行) | — |
| Mock LLM | MockLLMProvider | — |
| 工具分发 | ToolRegistry | — |
| 治理护栏(代码) | guardrail 12模式 | 缺 HITL 审批状态机 |
| 上下文+记忆 | context.py + JSON持久化 | — |
| 配置 | config.yml + env | — |
| **反馈闭环** | validate.py(幻觉检测) | **缺 test/lint 回灌** |
| **HITL 审批** | 无 | **缺 confirm_required 事件+状态机** |
| **主要贡献深入** | 六个维度各有一点 | **需选1个做深** |
| **Mock-LLM 全链路单测** | 分散的单元测试 | **缺集成测试** |
| **机制演示** | 无 | **缺3个演示脚本** |
| Docker CI | Dockerfile 存在 | **缺 CI 构建推送** |

## 执行计划 (共5步, 约2小时)

### Step 1: 反馈闭环 — test/lint 回灌

**文件**: `agent.py`
**机制**: 工具执行完成后，如果是 `file_write`/`file_edit`，自动运行 `shell_exec("python -m py_compile {file}"` 或 `shell_exec("pytest {test_file}")`，将结果作为 tool 反馈注入 messages，LLM 据此自我修正。

**实现**:
- 新增 `_auto_validate(state, tc_name, tc_params)` 函数
- 对 .py 文件写入后自动语法检查
- 对 _test.py 文件写入后自动运行测试
- 结果以 `role: system` 消息注入，带 `[自动验证]` 前缀

### Step 2: HITL 审批 — 危险操作暂停

**文件**: `agent.py`, `server.py`, `App.tsx`
**机制**: guardrail 拦截后不直接阻断，而是:
1. emit `confirm_required` {id, action, reason}
2. 前端弹确认框
3. 用户同意 → 后端 resume, 执行工具
4. 用户拒绝 → 返回 "操作已取消"

**实现**:
- `agent.py`: guardrail → `_emit("confirm_required", ...)` → 挂起
- `server.py`: 新增 `POST /api/confirm {confirm_id, approved}`
- `App.tsx`: confirm_required 事件 → 弹确认框 → POST to /api/confirm

### Step 3: Mock-LLM 全链路集成测试

**文件**: `tests/test_harness_integration.py`
**场景**:
1. 护栏拦截危险命令 → guardrail 返回 block reason → 不执行
2. 注入文件语法错误 → 自动验证捕获 → LLM 收到修正提示
3. 工具分发 + 参数校验完整链路

**实现**: MockLLM 预设多轮响应，验证 harness 行为

### Step 4: 机制演示脚本

**文件**: `tests/demo_mechanisms.py`
**脚本**: 不依赖网络/真实 LLM，确定性验证:
1. guardrail 拦截 `rm -rf /` — 断言 block reason
2. 注入文件写错 → auto_validate 捕获 → 断言回灌消息
3. HITL 确认流程 → 断言状态转换

### Step 5: Docker CI 构建

**文件**: `.github/workflows/ci.yml`
**改动**: 添加 docker build job，构建 backend + frontend 镜像
