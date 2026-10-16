# 插件声明、归属与资源作用域

## 已实现的基础契约

plugins.py 提供独立 PluginManager；PluginDeclaration 声明 requires、optional、provides 与 apply。安装可以先于依赖，插件进入 pending；依赖服务就绪后激活。服务只在 apply 成功且全部声明兑现后发布。停用提供者先停用必需依赖的消费者；仍启用的消费者等待服务恢复。插件借用服务，不获得其销毁权。

PluginContext 提供依赖快照，插件通过 provide 发布服务、scope.own 注册清理。register_tool 对接现有 ToolRegistry，inject_field 对接动态 AgentState；两者同时登记逆向撤销操作。注册冲突拒绝覆盖，避免卸载误删其他插件资源。

ResourceScope 记录 owner 与 APPLICATION/SESSION/TASK/REQUEST 生命周期标签，支持子作用域。关闭按逆序同步清理，继续尝试其他清理，失败汇总并保留失败清理项供重试；重复成功关闭无副作用。子作用域可提前关闭，父作用域关闭也会关闭子作用域。

apply 失败自动清理已登记资源，不发布服务；失败显式上报，不吞掉异常。停用/卸载不删除持久化数据或安装文件。磁盘包管理与运行资源生命周期分别处理。

## 当前集成与限制

这是新生命周期入口，已有 ToolPlugin 与 ToolRegistry 兼容保留，尚未将全部内置插件和应用启动迁移到 PluginManager。新插件可通过 PluginContext 注册旧注册表工具与 State 字段，但旧插件自动启停仍走原逻辑，不能同时由两套管理器管理同一注册项。

作用域标签不自动创建会话/任务；对应宿主仍须创建子作用域并在结束时 close。当前只支持同步清理，不支持异步 disposer、活跃调用排空、并发控制、持久化迁移及依赖循环诊断；缺失依赖会保持 pending。optional 服务为激活时快照，后续变化不自动刷新。控制面变更应在没有请求执行时进行。

下一步迁移：应用装配统一持有 PluginManager；将模型、工具、State 字段注册逐个改为 context-owned；最后替换旧启停路径。不能仅将旧 on_install 改名为 apply，仍需把实际创建的资源全部纳入 scope。
