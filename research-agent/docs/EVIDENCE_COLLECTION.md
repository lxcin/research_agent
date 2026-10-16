# 后台生命周期证据采集

当前实现使用 SQLite WAL 持久化发件箱，已替换先前的 UDP 尽力发送。

运行侧将结构化事件短同步提交到 DATA_DIR/evidence/lifecycle.db 的 outbox；独立收集器事务性归档到 evidence 并删除已归档项。scope+sequence 主键确保重放去重；收集器离线时积压保留。

```powershell
py -3 -m research_agent.evidence --database C:/path/to/data/evidence/lifecycle.db
# 单次归档，用于恢复和验收
py -3 -m research_agent.evidence --database C:/path/to/data/evidence/lifecycle.db --once
```

评测返回 pending、incomplete、complete 状态；只对闭合且序号连续的生命周期轨迹做完整检查。提交失败明确记录异常日志，不中断卸载；异常崩溃尚未通过心跳和租约与正常运行区分。采集器由部署环境启动；尚无归档容量策略。

可靠落盘需要生产者承担短同步提交，后续处理在独立进程。安全排空计数仍在运行侧，不依赖采集器。验收与剩余边界见 CLOSURE_REVIEW.md。
