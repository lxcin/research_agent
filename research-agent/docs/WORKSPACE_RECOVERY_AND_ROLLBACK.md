# 工作区创建、中断恢复与快照回退

更新：2026-10-15。

这些能力归属宿主，不进入思考内核。session_workspace 管理会话/worktree 绑定；workspace_recovery 核对中断操作；checkpoint 管理文件/暂存区快照、审阅和回退；reviews.db 保存证据，Git refs 保持快照可达。

## 创建与中断恢复

```powershell
research-agent session-workspace create --project <仓库根目录> --chat <稳定会话ID>
research-agent session-workspace recover-info --project <源目录> --chat <会话ID>
research-agent session-workspace recover --project <源目录> --chat <会话ID> --decision complete --review-token <审阅令牌>
```

创建要求源目录是仓库根目录、HEAD 已提交且位于分支上、业务文件没有未提交修改。不自动提交、暂存或丢弃人工改动；忽略文件不复制。已绑定会话复用原工作区，归档后须显式 restore，不自动回退到共享目录。checkout 必须位于源仓库外；工作文件和 index 独立，共享 Git 对象库。Docker 不负责创建工作区。

执行 Git 前，绑定索引持久化完整 preparing 意图：操作类型、版本、编号、项目/会话、before/after、仓库目录及文件系统身份。Git 后先发布 manifest，最后发布正式绑定。中断状态拒绝任务执行，不被当作不存在的绑定。

recover-info 只读检查，输出分支、HEAD、Git 登记、目录/manifest、可用 actions、预期 effects 和 review_token。decision 可以是 complete 或 cancel，但必须在结果中列出。执行前重新检查证据，变化后旧令牌失效。

| 中断位置 | complete | cancel |
| --- | --- | --- |
| 创建尚无分支和 checkout | 按保存的 base 创建 | 只移除未完成标记 |
| 创建已有合法 checkout | 补发绑定，保留已有编辑 | 不删除已产生资源 |
| 归档尚未删除 checkout | 无修改、未跟踪及忽略数据时移除 checkout，保留分支 | 保留目录、恢复活动绑定 |
| checkout 已归档 | 补发归档绑定 | 不偷偷重建目录 |
| 恢复尚未创建 checkout | 从保留分支创建 | 恢复归档绑定 |
| checkout 已恢复 | 补发活动绑定，保留已有编辑 | 不自动删除 checkout |

未知目录、独立修改的 manifest、变动的会话分支、prunable/locked 登记均保留并拒绝自动处理。源分支或仓库身份变化也拒绝。旧版没有完整意图时，只允许采纳已验证 manifest，或在分支、目录和登记均不存在时取消；其余需人工核对。正常绑定后来被外部删除/篡改，不伪造为一个中断操作。

恢复先检查 Docker/后台资源，取得会话、源目录、目标目录凭证，不 force、不删除分支、不递归删除未知目录。Git 和多文件发布不是事务。

## 整轮快照回退

```powershell
research-agent checkpoint list --workspace <实际执行目录>
research-agent checkpoint review --workspace <实际执行目录> --ref <快照引用>
research-agent checkpoint restore --workspace <实际执行目录> --ref <快照引用> --review-token <审阅令牌>
```

Python 同样先 review_checkpoint，再 restore_checkpoint(..., review_token=...)；缺失或伪造令牌拒绝。审阅输出文件恢复/删除清单、diff、截断/预览不可用标识、目标摘要、暂存区摘要。HEAD 保持不变：这是文件和暂存区回退，不是分支 reset 或撤销提交历史。

快照记录执行前受 Git 管理及未忽略文件、原始字节、权限和真实 index。创建用临时 index，不修改真实暂存区；暂存内容对应树单独固定引用。结束证据包含完整结束文件和 index，只封存一次，刷新不能批准后续人工编辑。仅文件工具执行也建快照，不依赖 Shell 插件。引用、完整 SHA 和列表均限定当前物理 worktree。

仅保留最近一轮撤销能力：下一轮先保存包含两轮间人工修改的新基线，再释放上一轮快照、结束版本和恢复备份的引用及完整快照元数据。旧 E 缺失不再阻断安全新轮；未应用冲突计划自动过期，令牌失效，独立冲突文件保留。实际后台写入、结果未知的后台任务、隔离标记及正在应用/部分失败的撤销仍阻断，保留恢复证据。旧 command 配置按 turn 处理。引用释放后安排[异步 Git 维护](STORAGE_POLICY.md)，由独立隐藏进程保守回收；保留对象宽限期，不保证磁盘立即缩小。

正常结束及安全停止的取消/失败可封存。副作用未知、清理未确认或后台仍写入时不封存。后台排空后，由最后一个受控写入凭证在解锁前固定 E，提交持久化 snapshot_ready；消费者只发布固定证据，不重新读取当前人工文件。前台/任务完成事件与 ready 消费协议见[后台快照消息](BACKGROUND_TASKS.md)。有可信 ready 时可在启动/接入时重放；捕获中断且缺少 ready 时标记 undo_unavailable，不补造旧 E。能否开始新轮由实际凭证、任务状态及隔离决定，不根据结束证据缺失推断任务仍在执行。

回退比较结束文件、权限、HEAD 和暂存区；人工在输出后修改、添加、删除或暂存均拒绝，不自动更新基线。任务开始前已有的人工工作区及暂存内容则随快照保留。

执行先备份，再逐文件校验、临时写入、fsync、原子替换和写后检查；只删除明确清单中的文件，不 clean/reset 整目录。最后通过 index.lock 和内容校验恢复开始前的真实 index。Agent 新增并暂存文件可删除，人工原有暂存内容完整恢复，原先未跟踪的文件仍未跟踪。

## 回退中断

回退记录 applying / partial / complete、目标、原始备份、尝试备份及进度。部分失败或进程退出后，重新 review 验证文件仍为已知结束值或恢复值，再发新令牌；上次未改动文件也必须重审。

index.lock 写专属标记并登记文件身份。只有匹配未完成回退记录，且内容为已知标记或准备好的目标 index 时，才在重新审阅后清理。外来、未知或部分损坏的锁保留供人工检查。真实子进程 os._exit 已覆盖，不承诺所有文件系统损坏阶段都自动修复。

## 边界与待实现

三方合并与选择性撤销已通过[统一服务](SELECTIVE_UNDO_PLAN.md)提供，包括完整结束快照、选择性暂存区处理、冲突产物及审阅入口；本文描述的严格快照恢复语义保持独立，仍拒绝后续人工编辑。

- 忽略文件不备份、不删除；它们占据恢复目标时拒绝覆盖。远程推送、API、数据库副作用需另行补偿。
- 链接路径、submodule、无法完整保存的特殊/冲突 index 保守拒绝。新版保存原始字节；旧版无法按过滤规则还原原始字节时仍拒绝。二进制可按验证后的原始快照恢复，不做合并。
- 多文件回退不是事务。外部编辑器不遵守锁，哈希检查与 replace/unlink 仍有竞争窗口；失败保留备份，不能承诺零覆盖风险。
- 保存的是可表达的权限位，不是完整 ACL、扩展属性或时间戳。审阅令牌识别计划版本，不是身份认证；备份引用也不等于一个自动批准的新回退目标。
- 新轮次逐文件 undo 保留执行前人工内容并合并后续不冲突编辑；同位置冲突、后续人工暂存及旧版证据不足继续拒绝。任意历史和按 hunk 撤销仍在[路线图](ROLLBACK_CONFLICTS.md)。

## 验证

真实临时 Git 覆盖创建/归档/恢复的 Git 前、Git 后和 manifest 后中断、取消、保留已有资源、未知目录、分支变化、旧记录及 CLI。回退覆盖暂存区、暂存新增、未跟踪、人工编辑/暂存拒绝、跨 worktree 隔离、不可变基线、外来/自有锁、部分失败重审、硬退出、取消和未知副作用。测试不操作开发项目分支。

快照与选择性撤销阶段为 632 passed / 8 skipped，证据为 artifacts/selective-undo-acceptance.xml。后台封存 outbox 和 Git 目标隔离补齐后，最新全量 **665 passed / 8 skipped / 9 个第三方弃用警告，339.59 秒**，线程异常按错误处理；证据为 artifacts/background-snapshot-outbox-acceptance.xml。默认跳过七项 Docker 真机测试及一个原有 Windows 超时语义用例；本轮另行真实 Docker 验收 **7 passed / 15.39 秒**，证据为 artifacts/background-snapshot-docker-acceptance.xml。
