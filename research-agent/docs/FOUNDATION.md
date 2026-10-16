# 基础契约与装配边界

更新：2026-10-15。

## 状态

应用长期持有稳定 Kernel；每次请求创建 RuntimeContext 与 RuntimeHost。内核输入输出为 KernelRequest/KernelResult；工作消息、阶段、失败计数保留在 run 局部，不访问整个 AgentState。

AgentState 是注入字段容器，默认核心字段来自 state_defaults；计划和沙箱分别由 plan.current、shell.session 插件字段持有。caps 仅兼容旧接口/日志。不要向内核接口追加项目、附件或具体工具字段。

## 配置

config.py 负责读取外部配置；kernel_factory 负责稳定策略装配。新增 ModelSettings 为不可变模型调用设置，集中工具选择温度、回答温度和输出上限；ProviderModelAdapter 可在构造时绑定设置快照，不在内核读取配置。兼容函数仍按调用读取设置，保留现有热配置行为。其他沙箱、插件配置目前仍由既有模块加载，尚未改成整个应用的统一配置快照。

## 模型适配

model_adapter.py 集中 LiteLLM 工具请求、参数解析、流式片段与 usage 归一化。agent/runtime 的旧函数保留为薄转发入口，便于兼容现有 patch 和遥测路径。LLMProvider.complete 继续服务压缩、提炼等普通文本调用，尚未合并进工具/流式协议。

ModelAdapter 定义 select/answer，ProviderModelAdapter 绑定供应商与 ModelSettings。RuntimeContext.model_adapter 可显式注入；默认生产路径继续使用原回调，保证 MeteredRuntime 原有计量包装正常运行。自定义 adapter 的工具选择计量尚未接入该包装，不应认为新端口已经完整替代生产路径。

适配器执行传输，不负责任务恢复。runtime/agent 的传输重试与流式入口统一使用 model_retry：仅暂时错误重试，永久错误中止；回答已经发布文字后失败时保留部分结果并停止，不重播。显式 adapter 的生产选择调用已纳入 MeteredRuntime，详情与限制见 [PATH_RETRY_METERING.md](PATH_RETRY_METERING.md)。

## 依赖关系

配置 → 外壳装配 → 稳定 Kernel + 请求 RuntimeContext → RuntimeHost → 模型适配与工具。

模型供应商属性访问集中到适配层；内核继续只通过 KernelHost 操作。基础状态采用窄接口隔离而非一次性迁移所有存储字段。这一轮落实模型传输去重和类型化配置/适配入口，剩余状态模型拆分及全局装配快照仍需后续迁移。

## 注入式 State 组件（已实现）

AgentState.components 是统一组件容器。StateComponent 声明 key、无参 factory 和可选 cleanup；inject 创建本 State 专属实例，重复注入同一声明保留实例，get 获取，remove 执行清理并卸载。工厂必须创建独立值，清理失败保留该组件并向调用方报告错误。

ToolPlugin.state_components 声明所属组件，key 必须以插件 id 加点号开头。ToolRegistry.bind_state 绑定 State 并注入启用插件的组件；安装/启用后注入，禁用/卸载后移除。注册表弱引用 State，不负责持久化组件。run_agent 在工具装配后绑定当前 State。

```python
ToolPlugin(id="example", state_components=[
    StateComponent("example.task", factory=dict, cleanup=release_task)
])
# 工具内访问：state.components.get("example.task")
```

自定义宿主也可直接通过 components.inject 注入非插件组件；注册表只移除它自身管理的 key。字段通过 codec 声明持久化及版本迁移；会话文件和执行检查点承担不同恢复范围。插件停用由资源区先拒绝新租约、排空活动调用，再移除字段和释放资源。组件化约束可信插件的所有权，不提供 Python 安全沙箱，也不承诺跨组件启停或清理的事务回滚。

## 修正：每个字段均由注入定义

AgentState 不再用 dataclass 固定声明业务字段，而是动态字段容器。user_input、conversation_turns、final_response 等默认字段在 state_defaults.py 的兼容字段包中声明，再由构造器注入。components 是字段存储机制，不再只是额外插件数据袋。

```python
state = AgentState(fields=[StateComponent("goal", str)], goal="write report")
state.goal = "review report"
state.components.remove("goal")  # 属性随字段卸载而消失
```

AgentState(fields=()) 为无业务字段容器；AgentState() 注入默认兼容字段包。未注入的公开字段禁止赋值；私有临时字段仍留兼容通道。插件字段通过插件声明注入/卸载，推荐经 components.view(owner) 访问。字段已声明所有权、类型与生命周期；业务数据采用副本和原子 update，资源引用采用明确的清理/fork 策略。AgentState 不支持 dataclasses.asdict，保存应使用组件协议；复制隔离数据、按 fork 派生资源，失败拒绝共用父状态。完整约定见 [TOOL_STATE_CONTRACTS.md](TOOL_STATE_CONTRACTS.md)。

插件归属与启动契约见 [PLUGIN_LIFECYCLE.md](PLUGIN_LIFECYCLE.md)。
