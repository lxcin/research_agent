# 会话存储与容器容量取舍

更新：2026-10-15。

保留 worktree 隔离代码，Docker 按需执行；共享稳定镜像与显式只读数据，合入后显式回收 checkout。未实现自定义 overlay 文件系统，不自动搬迁现有数据，也不定时删除用户会话。

## 容器容量

```yaml
shell:
  session: ephemeral
  max_containers: 2
  shared_mounts:
    - source: E:/datasets/reference
      target: /shared/reference
```

shared_mounts 默认留空，配置的宿主路径必须已存在；示例路径需要用户自行替换。容器目标限定为 /shared 下的子路径，挂载始终只读，拒绝父目录跳转、目标重叠及含逗号/换行的挂载路径。前台会话和一次性/后台容器共用同一配置和容量池；不会共享可变 .venv 或 node_modules，也不会复制数据到每个 checkout。宿主文件工具仍限定工作区，这些共享目录通过容器中的 Shell 使用。

容量凭证采用已有操作系统文件锁，启动前领取，确认容器关闭后释放；清理失败保留凭证及重试对象。达到上限立即报告容量不足，没有等待队列，不因此降级为本地 Shell。所有运行实例必须使用相同 data_dir 和 max_containers 配置。

删除命令明确报告对应容器不存在时，按已确认清理处理，允许幂等释放；daemon 连接失败或其他删除错误不能据此释放。一次性执行清理无法确认时还会隔离工作区，后台任务记录 recovery_required，避免继续写入。

统一采用 ephemeral：用户发送一次请求，到 Agent 本轮执行结束为一个回合（不是整个聊天窗口的存续时间）。容器在首个 Shell 命令时懒创建，回合内复用，正常完成、异常或取消后由宿主 finally 回收；没有 Shell 执行就不创建容器，等待下一条用户消息时不保留前台容器。已移除跨轮常驻路径；旧配置 shell.session=persistent 会记录迁移警告并采用 ephemeral，idle_timeout 不再生效。直接构造 SandboxConfig 时只接受 ephemeral。

显式后台命令拥有独立的一次性容器，可能在本轮回复后继续执行，完成、超时或取消时回收，并始终占用容量凭证。它们不会跨任务复用；本轮结束不强杀尚未完成的后台任务。代码文件保存在宿主 worktree，回收容器不删除文件；容器内临时环境不跨轮保留，稳定依赖应预装到共享镜像。

容器创建前持久化意图并写入安装/槽位标签。宿主崩溃后，在下一次请求或工作区变更前核对遗留容器，先取得槽位独占锁，再校验标签和完整名字并删除；活跃宿主持有的容器跳过。也可执行 research-agent sandbox-reconcile。无待核对资源时不探测引擎。此上限不统计手工或旧版无标签容器，不是 daemon 全局硬配额；详细边界见 [Docker 归属与验收](DOCKER_OWNERSHIP_AND_ACCEPTANCE.md)。

镜像层复用由 Docker 提供；bind-mounted worktree 仍是完整工作目录文件，不享受镜像可写层的写时复制。依赖预装镜像由部署方提供，当前实现不自动构建依赖镜像。

## 空间统计

工作区快照默认每轮一次，只提供最近一轮撤销能力；下一轮从实际工作区保存新基线后释放旧轮引用和完整快照元数据，包含本轮恢复备份、候选结果及旧消息。缺旧 E 不阻止安全换轮，未应用冲突自动过期；正在应用/部分失败的恢复、后台仍写入或结果未知继续阻断，保留证据。原始文件与暂存内容复用 Git 对象库；过期冲突目录独立保留，含人工解决结果，后续换轮也不自动删除，需要人工清理。其他产物在工作区外按登记文件清理。引用释放后异步安排保守 Git GC，不在对话内执行；没有物理字节硬配额。详见[选择性撤销与保留策略](SELECTIVE_UNDO_PLAN.md)。

## Git 后台回收

归属宿主应用维护服务 git_maintenance，不进入思考内核，也不是会话里的 Shell 后台任务。释放旧快照引用后，前台只解析 common Git 目录并提交小型 SQLite 意图，不等待 GC。队列位于 data_dir/git_maintenance.db；独立隐藏 Python 进程执行实际 Git 回收，宿主或聊天窗口退出不要求等待它。

按规范化 common Git 目录及文件系统身份合并任务，同项目多个 worktree 共享一条维护记录。首次延迟 30 秒；重复请求不延后原有待执行时间；成功后同一仓库最多每六小时运行一次。GC 期间到达的新释放请求增加 generation，完成时只确认领取的 generation，其余保留到下一次执行，避免丢通知。

每个 data_dir 通过 OS 锁只保留一个维护 worker，串行处理各仓库，不按会话创建常驻进程。队列空后释放锁并退出；检查队列为空与释放锁在发布事务边界内协调，避免退出期间丢失唤醒。CLI/API 服务启动及新入队会恢复 pending/running 工作；worker 崩溃后可重领，不把 running 当作已完成。

回收执行普通 Git GC，明确保留至少两周对象宽限期，不使用 prune=now、--force 或 aggressive。不修改业务文件、index 或分支，不主动压缩 refs、更新 commit graph、过期 reflog 或清理 worktree 登记；已引用的开始/结束快照、候选和恢复备份由 Git 可达性保护。其他 worktree 的分支、index 和 reflog 仍是 Git 根。普通 Git 写入可能与 GC 并发，因此保留宽限期，不能为了即时释放空间改成零宽限。

Git 自身 gc.pid 与本应用 worker 锁共同协调维护；遇到外来锁只延后，不删除锁文件。执行前复核目录真实位置和文件系统身份；仓库被替换或删除时 blocked，避免把旧任务用于新仓库。入队识别仓库与实际回收共用 host_git_environment，清除继承的 GIT_DIR / GIT_COMMON_DIR / GIT_WORK_TREE、替代 index、对象目录、namespace 及进程内 Git 配置覆盖，防止请求仓库 A 却登记了环境变量指定的 B。失败记录 error、attempts 并指数退避，最多五次后 failed；新释放请求或显式 request 可再次排队。单次 Git 执行超时五分钟，限制打包线程与窗口内存；超时/异常不会转为前台 GC，也不撤销已成功的新轮基线。

```text
research-agent git-maintenance status
research-agent git-maintenance request --workspace <任一 worktree 或源仓库>
```

request 只排队，不同步回收。状态包含 pending / running / idle / failed / blocked、generation/completed、下次时间和错误。入队或 worker 启动失败不阻断对话；启动失败保留持久化意图，入队本身失败写宿主日志，后续释放可再次提交。

异步不代表立即回收：年轻的无引用对象继续处于宽限期，reflog/其他分支仍引用的内容也不会删除。后续维护才会回收已过期且不可达的对象；没有新维护请求时不提供定时过期清扫保证。任务成功表示 Git GC 成功，不承诺回收固定字节数。共享仓库外部 Git 写入和磁盘 I/O 仍受 Git 自身并发规则约束。

专项验收：tests/test_git_maintenance.py 共 **19 passed**，包括入队/执行一致的目标隔离、进程内配置覆盖清理、真实普通 GC 删除过期对象、保留近期对象/其他 worktree 暂存/reflog、真实隐藏 worker、硬退出重领、退出期间入队、跨 worktree 去重、GC 期间新请求、失败退避、超时、外来锁和仓库身份变化。所有回收测试限定独立临时仓库，不对开发项目执行 GC。

```text
research-agent session-workspace storage --project <仓库根目录> --chat <会话ID>
```

返回 logical_bytes、files、skipped_links、archived。统计会话 checkout 的逻辑文件字节，不跟随符号链接或目录 reparse point（包括 Windows junction）；不包含共享 Git 对象库、Docker 镜像/可写层、外置共享数据或其他日志。它不是物理磁盘占用、容量配额或去重后的精确计量。并发修改时统计可能失败，适合空闲时查看。

## 安全归档与恢复

先审阅、提交会话修改并显式合入源项目，然后执行：

```text
research-agent session-workspace info --project <仓库根目录> --chat <会话ID>
research-agent session-workspace archive --project <仓库根目录> --chat <会话ID> --expected-project-head <源HEAD> --expected-session-head <会话HEAD>
research-agent session-workspace restore --project <仓库根目录> --chat <会话ID>
```

archive 先核对并清理失去宿主的容器，再获取项目和会话凭证、核对绑定与两个 HEAD；要求会话提交是源 HEAD 的祖先，并拒绝任何已修改、未跟踪或 Git 忽略数据。包括 .env、实验结果和缓存；“代码合入”不意味着这些数据已保存。活跃请求和后台任务仍持有凭证时拒绝归档。失败沙箱先确认关闭，再执行 git worktree remove，不使用 --force，不自行递归删除目录，不删除分支。

成功后持久记录 archived 与 archived_head。再次发消息会明确提示归档状态，不退回主目录，也不隐式创建副本；restore 从保留分支重建相同 checkout，并恢复强制绑定。若分支已变化/丢失或原路径被其他文件占用，拒绝恢复。运行历史及提交继续保留。

创建/归档/恢复前写完整 preparing 意图；中断后用 session-workspace recover-info 检查证据，再用 recover 和审阅令牌选择 complete/cancel，不自动删除未知目录或分支。流程见[工作区创建与回退](WORKSPACE_RECOVERY_AND_ROLLBACK.md)。多个文件和 Git 操作不构成事务；外部编辑器不遵守服务锁，校验后新建文件仍存在竞争窗口。归档必须在用户停止外部写入后进行。

## 尚未实现

自动保留期限、总磁盘字节配额、定时过期回收、容量等待队列和主机原生写时复制尚未实现。容器内不映射共享 Git 元数据，改用宿主 git_inspect 查询。本机 Docker 引擎已恢复，六项真实 Docker 验收已全部通过。

验证使用真实临时 Git 仓库与模拟 Docker 返回值：覆盖归档/恢复、未合入与活跃请求拒绝、修改/未跟踪/忽略文件保护、CLI 查询、只读挂载及非法配置拒绝、容量占满与释放、清理失败保留容量，以及一次性/可取消执行共用容量池。

Git 入队目标隔离与后台封存 outbox 补齐后，全量验收为 **665 passed / 8 skipped / 9 个第三方弃用警告，339.59 秒**，线程异常按错误处理，证据为 artifacts/background-snapshot-outbox-acceptance.xml；Git 维护专项 **19 passed**。本轮额外真实 Docker 验收 **7 passed / 15.39 秒**，证据为 artifacts/background-snapshot-docker-acceptance.xml。详见[工作区创建与回退](WORKSPACE_RECOVERY_AND_ROLLBACK.md)、[Docker 归属与验收](DOCKER_OWNERSHIP_AND_ACCEPTANCE.md)及[后台任务生命周期](BACKGROUND_TASKS.md)。
