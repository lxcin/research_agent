# P0 收尾记录

## 本轮交付

提供 execution inspect/reconcile/resume CLI。核对时使用工作区和会话锁，禁止修改正在执行的任务记录。只允许核对 started 状态；必须附依据。已确认完成时提供 ToolResult，已确认未执行时允许后续重新执行；两者均写事务审计记录，不提供“忽略不确定性继续”的按钮。

```text
research-agent execution inspect RUN_ID
research-agent execution reconcile RUN_ID CALL_ID --decision completed --note "外部记录核对依据" --result-file verified-result.json
research-agent execution reconcile RUN_ID CALL_ID --decision not_executed --note "确认请求从未被接受的依据"
research-agent execution resume RUN_ID
```

not_executed 不能仅凭“暂时没看到结果”判断；需要操作者确认不会再发生迟到副作用。人工确认完成涉及插件私有状态时，ToolResult 本身不一定足够，仍需插件专门恢复协议。inspect 默认不输出工具参数，但包含原始任务输入，输出应按用户数据管理。

子代理模型调用已接入可取消等待与默认传输超时。恢复检查点保留原工作区 checkpoint 引用，回合恢复重新显示已存在的持久提案。对话、压缩、语义检查与情景落库各有去重机制；不声称模型费用或所有旁路事件 exactly-once。

## 验收

全量 444 passed / 1 skipped；最后工作区引用与回合后调整后专项 33 passed。线程警告按错误处理，余下 8 条依赖弃用警告。新增核对成功后不重复副作用、确认未执行后允许重试、活跃执行拒绝核对、核对审计留痕与 CLI 查询。

Docker 真机验收仍保留：CLI 可用，但 dockerDesktopLinuxEngine 管道不存在。没有擅自启动 Docker Desktop。取消失败后的隔离和参数路径仅经模拟验证。

## 收尾结论与剩余项

本轮代码与已覆盖验收完成，P0 不能无条件全部关闭。保留项为 Docker 真机停止验收、任意插件/子代理执行位置的完整检查点、原生异步资源恢复，以及不合作编辑器的极短竞争窗口。远程补偿不是框架通用可自动实现的保证，必须由领域工具提供。三方合并/只撤 Agent 补丁仍按路线图待实现，当前继续拒绝混写。
