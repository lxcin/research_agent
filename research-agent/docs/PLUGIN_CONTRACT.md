# 插件契约（Plugin Contract）

> 本文是 PaperPilot 的**能力插件规范**：任何内置插件、用户插件、Agent 生成的插件都必须满足。
> 代码依据：`tools/schema.py`、`tools/__init__.py`、`tools/mcp_loader.py`。
> **状态**：内置/用户插件契约已实现；**Agent 自写插件暂不实现**——其契约见 §7（仅规范，供后续按此实现）。

## 1. 两级粒度

| 粒度 | 类型 | 谁使用 | 含义 |
|---|---|---|---|
| 工具 | `ToolSchema` | Agent / LLM | 一个可调用动作（与 MCP tool 同构） |
| 插件 | `ToolPlugin` | 宿主/工程 | 一个能力单元：一组工具 + 生命周期 + 依赖 + 磁盘足迹 |

一个插件可以**没有工具**（行为插件，如 `diagnostics` / `telemetry` 的采集侧）。

## 2. `ToolSchema` 契约

```python
ToolSchema(
  name: str,                 # 全局唯一，snake_case；注册时按描述去重(相似度>0.80 拒绝)
  description: str,          # 面向模型的一句话；同类工具需有区分度
  parameters: dict,          # JSON Schema，与 MCP inputSchema 一致
  handler: Callable,         # (params, llm, state, emit) -> ToolResult
  category: str = "general", # builtin | skill | user | mcp
  triggers: list[str] = [],  # 仅 skill 类工具用
  plugin_id: str = "",       # 归属插件（必填，注册时校验一致）
  group: str = "",           # 插件内子分组
  requires_approval: bool = False,  # True → 宿主 HITL 审批
  side_effect: bool = True,         # True → 可能改变外部状态
)
```

**handler 契约**：
- 入参 `params`（已过参数校验）、`llm`、`state`、`emit(event_type, data)`；
- 返回 `ToolResult`：`ToolResult.ok(**data)` / `ToolResult.fail(reason)`；
- `data` 需 **JSON 可序列化**（内核会 `json.dumps` 回灌模型）；
- 只读工具须 `side_effect=False`；写/危险工具须 `requires_approval=True`；
- 禁止抛异常（注册表会兜成 `fail`，但应自行捕获）。

**与 MCP 对齐**：`name / description / parameters` 即 MCP tool 的对应字段；宿主专有字段
（`requires_approval / side_effect / plugin_id / group / triggers / category`）**不进入 MCP 载荷**。

## 3. `ToolPlugin` 契约

```python
ToolPlugin(
  id: str,                       # 唯一；config plugins.<id>.enabled 控制开关
  label: str = "",
  depends: list[str] = [],       # 依赖插件 id（安装/卸载时校验）
  tools: list[ToolSchema] = [],
  enabled: bool = True,          # 默认启用态（config 可覆盖）
  is_core: bool = False,         # core 不可卸载
  # 磁盘足迹（卸载时按此清理）
  owned_files: list[str] = [],
  owned_packages: list[str] = [],
  test_files: list[str] = [],
  data_dirs: list[str] = [],
  # 生命周期钩子（可选，均为无参可调用）
  on_install / on_uninstall / on_enable / on_disable = None,
)
```

**注册表语义**（`ToolRegistry`）：
- `install_plugin`：依赖检查 → 校验工具 `plugin_id` 一致 → 按 enabled 态注册工具；
- `enable/disable_plugin`：运行时增删工具 + 持久化 `plugins.<id>.enabled`；
- `uninstall_plugin`：core 拒绝、被依赖时拒绝；注销工具；`remove_plugin_disk` 清理足迹。

## 4. 执行后端

| 后端 | 适用 | 说明 |
|---|---|---|
| **in-process** | 内置/低风险、需宿主上下文（state/emit） | 直接调用 handler |
| **MCP 子进程** | 外部/高风险、需隔离 | 经 `mcp_loader` 注册为 `mcp_<name>`，`plugin_id="mcp"` |

> 生成/第三方能力优先走 MCP 子进程（真隔离、独立 env/cwd、崩溃不牵连宿主）。

## 5. 治理

- **审批**：`requires_approval=True` → 宿主发 `confirm_required`，60s 超时默认拒绝（`shell_exec` 另叠加 guardrail）；
- **只读**：`side_effect=False` 的工具不进 keep/undo 提案流；
- **参数校验**：`validate_tools` 在分发前检查必需参数；
- **去重**：注册时按 description 相似度（>0.80）拒绝近似工具；
- **可观测**：工具经 `emit` 进入事件流，`telemetry`/`audit` 可度量。

## 6. 用户插件（已实现，`data_dir/`）

- **技能**：`data_dir/skills/*.md`（YAML 头：`name/description/triggers/version` + 正文），trigger 命中注入；
- **用户工具**：`data_dir/tools/*.py`（导出 `ToolSchema`），由 `ToolRegistry.load_from_dir` 加载；
- 均为**数据/代码**，须经人工确认后生效（自进化流程）。

## 7. Agent 自写插件契约（**暂不实现**，仅规范）

若将来启用"插件模式"，生成物必须满足：

**形态**：一个 **MCP stdio server** + 一份 `manifest.json`，由宿主包装为 `ToolPlugin(id="user_<name>")`。

```
data_dir/tools/<id>/
  server.py        # MCP server，暴露 tool(s)
  manifest.json    # { id, version, tools:[{name,description,inputSchema}],
                     #   capabilities:[...], tests:[...] }
  test_<id>.py     # 最小测试（必需）
```

**能力声明 `capabilities`**（最小权限地基；默认**空**，逐项授予）：
`fs.read / fs.write / net.out / shell.exec / code.exec / memory.write`（可按域细化）。

**准入校验（确定性，纯代码）**：
1. `py_compile` 全部通过；
2. AST 扫描**禁止** `eval / exec / os.system / subprocess / __import__`（除非已授予 `code.exec`）；
3. 导入白名单；
4. `manifest.tools` 的 `inputSchema` 合法；
5. 凭据扫描（`sk-…` 等）；
6. `capabilities ⊆ 已授予`，否则拒绝安装；
7. **必须附测试**，且在隔离进程跑通（起 server → list_tools → smoke call）。

**生效流程**：模式开关 → 生成 → 校验 → 授予能力 → 人工审批 → 注册（`mcp_loader`）→ **默认禁用 canary** → 观察 → 启用；失败/异常 → 自动禁用 + `uninstall`（按 `owned_files`）+ `records.status=rolled_back`。

**红线**：不允许 Agent 改系统提示词/核心代码；无审批不落盘；无能力声明不得安装。

## 8. 命名与版本
- 插件 id：小写 snake/kebab，用户插件前缀 `user_`；
- 工具名全局唯一；技能文件名 `_slug(name).md`，同名更新**递增 `version`** 并记录 `parent`；
- 事件契约新增字段应向后兼容（`audit`/`telemetry` 依赖事件流）。

## 9. 最小示例

**in-process 插件**：
```python
from research_agent.tools.schema import ToolSchema, ToolResult

def _hi(params, llm, state, emit):
    emit("tool", {"tool": "hello", "status": "done"})
    return ToolResult.ok(greeting=f"hi {params.get('who','')}")

hello = ToolSchema(name="hello", description="打招呼", parameters={
    "type": "object", "properties": {"who": {"type": "string"}}, "required": []},
    handler=_hi, category="builtin")
# ToolPlugin(id="demo", tools=[hello])
```
