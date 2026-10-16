# 会话独立分支与 Git worktree（试用）

创建、归档和恢复中断已有显式 recover-info / recover 入口；整轮快照已有带审阅令牌的 checkpoint review / restore。现行契约见[工作区创建与回退](WORKSPACE_RECOVERY_AND_ROLLBACK.md)。

更新：2026-10-15。

## 使用方式

本次提供显式试用开关，原共享目录模式继续兼容，不自动迁移现有会话或修改当前项目分支。

```text
research-agent chat --workspace <Git仓库根目录> --isolate-session
```

API 首次请求使用稳定 chat_id，并设置 isolate_session=true。没有提供 chat_id 时服务生成一个，并通过 session_workspace 事件返回；后续必须复用此 id，开关可以省略。事件还包含 project_workspace、实际 workspace 与 branch；文件预览和提案应使用实际 workspace，不能继续把项目根目录当作修改位置。CLI /where 显示项目、执行目录、会话和分支；/new 创建另一份隔离会话。

会话隔离一旦成功，data_dir/session_bindings 按 chat_id 持久记录归属，和 worktree 的 session.json 相互核对。开关采用三态：true 表示创建/使用隔离会话，省略或 null 表示沿用绑定，false 显式请求共享模式。已隔离会话传 false 会拒绝执行，不会切回源目录；传入另一个项目或另一会话目录同样拒绝。兼容旧客户端时，应省略该字段而不是固定发送 false。没有绑定的旧会话继续使用共享模式。

每次绑定会话请求都返回 session_workspace 事件，包括省略开关后的自动路由；新的 AgentState 或服务重启不会丢失磁盘绑定。绑定前已经解析为共享模式的请求，在取得执行锁后再次检查绑定，防止并发首次隔离造成主目录写入。绑定记录损坏、目录/manifest 丢失或创建中断时停止，不退回共享模式；创建临时状态标为 preparing，保留待人工核对。旧版本只有 session.json 的会话在首次访问时补建绑定索引。所有应用实例仍需使用相同 data_dir，绑定和锁并非跨独立部署的全局目录服务。

Python 入口：run_agent(..., workspace_dir=project_root, chat_id=stable_id, isolate_session=True)。恢复命令从执行日志读取实际 worktree 路径，不创建新分支，并校验该目录与 chat 绑定。

## 当前行为

每个项目路径+chat_id 映射到稳定的 codex/session-<hash> 分支，以及 data_dir/session_worktrees/<hash>/checkout。绑定记录在 checkout 旁的 session.json，原子保存。数据目录必须在源仓库外。

首次创建从源仓库当前 HEAD 派生，要求仓库已有提交、位于分支上、业务文件没有未提交改动；不静默忽略人工改动，不自动提交或暂存它们。框架 .research-agent 元数据不作为业务脏文件。未跟踪文件同样需要处理；Git 忽略文件（例如 .env、依赖目录）不会复制，按会话另行配置。

同一会话复用原 worktree，包括未提交修改；不会每轮重建、重置到项目最新 HEAD 或自动 rebase。分支被切换、目录丢失、绑定不一致时拒绝继续，不强制覆盖。创建中途失败可能遗留分支/目录，需显式核对，不自动删除用户资源。

不同会话请求锁对应不同实际目录和 chat，因此可以并行；同一会话仍互斥。Docker 按单轮任务懒创建，挂载对应 checkout 到 /work，轮内复用、结束回收，不跨轮常驻。宿主 Git 提案、检查点、接受与撤销支持 worktree 的 .git 文件；检查点引用加入工作区命名空间和随机后缀，避免跨会话碰撞。逻辑项目和会话历史仍归属源项目，源目录内不自动落入会话代码修改。

这不是每个窗口一个独立仓库。Git 对象、refs 和部分配置仍由 worktree 共享，仓库维护应协调执行；对话元数据和项目进度也不是文件快照的一部分。Shell 是可信执行能力，显式访问源目录或操纵其他 refs 仍可能影响别的会话，worktree 不构成恶意插件/命令的安全沙箱。

## 审阅与合入

CLI keep/undo 操作会话的实际目录；keep 提交到会话分支，不自动进入源项目。

```text
research-agent session-workspace info --project <仓库根目录> --chat <chat_id>
research-agent session-workspace integrate --project <仓库根目录> --chat <chat_id> --expected-project-head <审阅的源HEAD> --expected-session-head <审阅的会话HEAD>
```

先在会话中审阅、接受并提交修改，再从 info 核对两个 HEAD。integrate 同时取得源目录和会话目录锁，核对源分支、两个 HEAD 与工作区清洁状态，只执行 merge --ff-only。存在分叉、未提交修改或基线变化时拒绝，不留下冲突合并状态、不自动更新旧审阅基线。

两个会话从同一 base 修改时，第一个可能快进合入，第二个通常需要人工审查后的 rebase/merge。没有自动冲突合并、定时同步或 Web 合入界面。已新增合入后显式 archive/restore 和 storage 查询，详见 [STORAGE_POLICY.md](STORAGE_POLICY.md)。删除聊天不会删除未合入分支，也不自动删除包含数据的会话目录。

HEAD 参数是防陈旧检查，不是身份认证或完整审阅令牌。外部编辑器不遵守锁，检查与 Git 更新之间仍有乐观并发窗口。

## Docker 边界与验证

已验证不同会话使用不同宿主目录。Git 查询通过注入的宿主 git_inspect 工具完成（status/diff/log/show），绑定当前 checkout；提案、检查点及提交仍走已有宿主入口。不把共享 Git 元数据暴露给容器，容器内原始 git 不保证可用。本机 Docker 引擎恢复后，六项真实验收全部通过，包括容器修改 worktree 后由宿主 Git 查询、源目录保持原内容，以及崩溃回收时保留另一活跃窗口。归属与恢复契约、验收命令和故障记录见 [Docker 归属与验收](DOCKER_OWNERSHIP_AND_ACCEPTANCE.md)。

回归使用临时仓库运行真实 Git，覆盖不同会话文件及暂存区隔离、未提交修改重开、脏源拒绝、分支切换拒绝、并行执行与历史归属、宿主提案提交、快进合入、分叉和陈旧基线拒绝、真实运行时写入/检查点/提案、恢复绑定以及挂载参数。所有测试使用临时仓库，不在开发项目上试提交。

强制绑定收尾验收：全量 520 passed / 1 skipped，8 条依赖弃用警告，线程未处理异常按错误处理。新增验证漏传开关自动路由、显式共享拒绝、跨项目拒绝、旧 manifest 升级、缺失 manifest 拒绝、创建中断保留 preparing、首次绑定准入竞争以及 API 三态字段。
