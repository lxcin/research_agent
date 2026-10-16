# Docker 归属、Git 边界与真实验收

更新：2026-10-15。

## 资源归属

内核不参与容器和 Git 管理。Shell 插件声明能力，runtime 持有单轮沙箱；后台命令持有独立的一次性沙箱。sandbox 负责创建、执行与关闭，docker_ownership 负责容量凭证、持久化创建意图及崩溃后的核对回收。

创建顺序：取得槽位 OS 文件锁 → 核对并回收该槽位遗留容器 → 原子写入并 fsync 创建意图 → 创建带归属标签的容器。关闭时确认删除或明确不存在后，清除意图记录并释放槽位；任何一步不确定都保留重试对象。一次性命令也明确确认删除，不仅根据 Docker 客户端退出码推断容器已消失。

标签为 io.research-agent.managed=1、scope（规范化 data_dir 的哈希）、slot。容器名由 scope 和 slot 确定，不靠随机名字或 PID 猜归属。同一槽位名称也用于阻止旧宿主的延迟创建与替代容器并存；daemon 在途创建不构成文件系统事务，创建冲突仍需明确失败和再次核对。

恢复时发现本安装的容器和持久化槽位记录，再尝试取得对应 OS 锁。锁忙表示另一窗口/进程仍持有资源，跳过；取得锁后重新检查标签和完整名字，只删除符合契约的容器。容量配置缩小时，也扫描旧范围中的槽位。旧版没有归属标签的 pp-sbx 等容器、手工容器和其他 data_dir 的容器均不自动删除。

首次请求及工作区创建、合入、归档、恢复和提案变更前进行恢复检查；每次新建容器还在槽位锁内再次核对。也可显式执行：

```powershell
research-agent sandbox-reconcile
```

没有待核对的创建记录时，请求入口不探测 Docker；普通文件编辑仍可直接进行。有待核对记录但引擎不可达时，拒绝新操作，不能静默转成本地写入。显式核对命令还会扫描有标签但记录已缺失的容器。

所有实例须保持 data_dir、Docker daemon/context 和容量配置一致；不能把此机制视为 daemon 全局硬配额。切换 daemon/context 时必须先确认旧环境已清理，不能拿新 daemon 的空列表证明旧环境无资源。删除/移动 data_dir 会丢失自动恢复的归属依据。没有定时守护进程：宿主崩溃后，在下一次受控操作或显式核对时回收。

## Git 走宿主契约

不将主仓库 .git/common-dir 挂载给容器，也不把任意容器命令转发成宿主 Shell。

filesystem 插件注入 git_inspect，只接收 status、diff、log、show 四种查询；绑定当前实际工作区，支持 linked worktree。revision 限制为 HEAD 或提交哈希，路径限定在工作区并按 literal pathspec 处理；关闭 pager、外部 diff、textconv、签名显示和 fsmonitor，移除继承的 GIT_* 重定向环境变量，限制输出大小和执行时间。

提交、接受/撤销提案、检查点和合入仍由已有宿主流程处理。Docker 内原始 git 不保证可用，不自动安装 Git，也没有提供任意 git argv 的宿主后门。这是查询能力的完整入口，不是让容器成为独立的 Git 管理者。

## 验收入口

普通回归运行模拟 Docker 测试、真实临时 Git 仓库测试，以及真实子进程 os._exit 后锁释放/创建记录保留测试，不依赖 Docker daemon。

真实验收入口是 tests/test_docker_real.py，默认不运行。显式开启后，引擎或镜像缺失是准备失败，不会降级为跳过。需要 Linux Docker 和本地镜像（默认 python:3.11-slim）；镜像下载由使用者显式执行，测试不自动下载。

```powershell
docker info
docker pull python:3.11-slim
$env:RESEARCH_AGENT_DOCKER_TESTS = '1'
py -3 -m pytest tests/test_docker_real.py -v --junitxml=artifacts/docker-real-acceptance.xml
Remove-Item Env:RESEARCH_AGENT_DOCKER_TESTS
```

可通过 RESEARCH_AGENT_DOCKER_IMAGE 选择已有兼容镜像，镜像需具备 sh、sleep、cat、echo 和 touch。验收仅使用临时工作区和独立 data_dir，不操作开发仓库分支。

覆盖六个用例：轮内复用、资源限制、只读共享挂载及结束回收；前台超时回收；前台取消回收；后台一次性命令超时回收；子进程直接退出后回收其容器且保留另一活跃窗口；容器修改 worktree 后由宿主 Git 查看且源目录不变。

## 本次执行结果

普通全量回归：552 passed / 7 skipped / 8 个第三方弃用警告。跳过包含六项默认未启用的真实 Docker 验收和原有一个 Windows 超时语义用例。

首次单独显式运行真实验收：6 errors，均发生在 Docker 引擎连接检查阶段。dockerDesktopLinuxEngine 命名管道不存在，Docker Desktop 4.63.0 启动日志显示服务 Unix socket 访问失败。这次失败记录不能计为容器功能已验证。

尝试保留并重建 Docker/run 临时目录后，启动继续暴露 docker-secrets-engine/engine.sock 同类故障；该目录的局部改名也被 Windows 拒绝。没有重置 Docker、删除镜像/卷或调整权限。运行目录备份保留为 LOCALAPPDATA/Docker/run-stale-6aff2d3b9e3e40e7a2546d5ad23312b3；失败的服务目录移动还留下同级 docker-secrets-engine-stale-6f6365623e384feb8726f7a605d6dada 目录。故障引擎进程已停止，真实验收需在修复本机 Docker 引擎后重跑。

### 本机恢复及重新验收

随后已恢复本机 Docker：Windows 普通权限及管理员权限均无法改名 docker-secrets-engine 目录；在 WSL Ubuntu-20.04 中核对其仅含 engine.sock 后，通过 os.rename 将整个目录改名保留。Docker Desktop 重建运行目录后，Engine 29.2.1 / linux/amd64 恢复，原有镜像、卷和业务容器可见。没有恢复出厂设置、注销 WSL 分发版或清理镜像/卷。

本次额外保留的目录：LOCALAPPDATA/Docker/run.saved-338ea18ba2254c6a9ec6400a4af6ce29，以及 LOCALAPPDATA/docker-secrets-engine.saved-wsl-055baa0dc16e44b4a108b7f8fee1f9fc。它们作为故障套接字备份保留，不自动删除。失败的管理员尝试单独记录在 artifacts/docker-runtime-repair.json；恢复成功结果见 artifacts/docker-runtime-recovery.json。

使用已有 python:3.11-slim 镜像重新执行全部六项真实 Docker 验收：**6 passed，13.90 秒**。确认轮内复用/结束清理、只读挂载及限制、前台超时与取消、后台命令超时、宿主崩溃后清理且不影响活跃窗口，以及容器 worktree 写入与宿主 Git 查询。JUnit 成功证据已更新至 artifacts/docker-real-acceptance.xml。

这次修复恢复了本机运行状态，不能证明 Windows/Docker 的套接字故障根因已消除。[Docker 项目中的同类故障报告](https://github.com/docker/for-win/issues/15064)同样记录了失效 AF_UNIX 文件和保留父目录的恢复方式。后续应优先正常关闭 Desktop；若复发，先检查相同启动日志，不直接重置数据。

### 后台控制补充验收

后台任务迁移到宿主管理后，真实验收增加第七项：前台请求已结束时，通过执行编号取消后台 Shell，确认 cancelled、Docker 容器被删除且工作区凭证释放。当前 **7 passed，15.75 秒**，JUnit 证据已更新。槽位记录的 data_dir 也固定在创建时，避免清理时因进程配置变化误碰另一个目录。完整控制契约见 [后台任务生命周期](BACKGROUND_TASKS.md)。
