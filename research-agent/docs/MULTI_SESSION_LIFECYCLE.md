# 多窗口与资源排空

ResourceScope.acquire 为调用取得凭证，finally 归还。drain 关闭新凭证入口并等待活跃计数归零，close 再执行清理。超时保留资源，可继续等待。PluginContext.register_tool 自动为同步工具执行包装凭证。

ServiceRouter.publish 原子发布新实例；borrow 固定调用所使用的版本。新请求使用新版本，已有请求继续旧版本。旧作用域进入 retired 集合，reap 显式排空清理；超时仍保留记录。remove 撤销全局路由并等待旧调用结束。

SessionBinding 提供独立窗口/会话入口。关闭一个绑定只拒绝该窗口新调用并等待本窗口现有调用；不会关闭共享应用资源，其他窗口仍可借用。

当前仅支持同步调用，后台任务和流式消费须持续持有凭证直到真正结束。一个服务版本使用独立作用域，不应把同一作用域绑定多个名字后分别移除。整组依赖插件热替换、独占资源交接、异步排空与自动 retired 回收尚未实现。

PluginManager 负责依赖逆序释放；ServiceRouter 不推断依赖。生产 ToolRegistry 安装/启用/禁用/卸载已统一转交 PluginManager，ToolPlugin 保留为兼容声明格式。CLI/API 的 register_builtins 入口采用相同生命周期。同步工具持有插件及会话凭证；动态 MCP 工具注册和连接清理进入插件作用域；后台 Shell 线程持有凭证直到实际结束。

ToolRegistry.close_session/reopen_session 提供会话级入口控制。会话关闭不卸载全局插件。当前控制面需串行调用；任意异步插件、整组依赖热替换及模型/诊断回合包装的完整资源归属尚未完成。不可将工具迁移等同于整个应用全部横切能力已经支持安全热卸载。

2026-10-15：会话关闭标记和后台取消请求已持久化到 background_tasks.db。会话关闭取消关联后台任务及请求，并等待共享 chat 凭证（包括其他进程和阻塞模型传输）释放；HTTP/CLI 控制不受后台工作区写入锁阻塞。具体 API、状态和失败边界见 [后台任务生命周期](BACKGROUND_TASKS.md)。
