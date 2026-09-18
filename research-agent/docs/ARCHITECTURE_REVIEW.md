# PaperPilot 架构梳理 — 个人助手方向 (ARCHITECTURE_REVIEW)

> 状态: v2 定稿核心决策 | 日期: 2026-09
> 背景: 项目从"论文研究工具"逐步演进出强健的 agent harness（自研循环/治理/可插拔/记忆/诊断）。
> 但 UI/API 仍残留"研究工具"外壳，且把实现细节（工作区路径、论文库、图谱、进度总结）暴露给用户。
> 本文件梳理"作为个人助手，什么留下、什么删、沙箱/记忆/prompt 长什么样"。

---

## 0. 结论先行（TL;DR）

1. **产品定位 = 通用个人助手**。论文检索退化为普通工具，研究外壳（论文库/图谱/上传）删除。
2. **核心机制：git-based 沙箱 + 逐文件提案**。改动以文件级 git diff 呈现，交互对齐 opencode / Claude Code：
   - 可**一键同意全部**；
   - 可**逐文件**：先拒绝某些文件的更改，再同意剩余全部；
   - 同意才落地(commit)，拒绝即丢弃。
3. **工作目录**：用户**指定并绑定**；未指定也落用户工作目录（默认目录）。不做内部隐藏 data_dir 目录。
4. **删除**：论文库 UI+API、图谱、上传 PDF、**工作区/项目进度总结**、零散路径 UI。
5. **保留**：自研 agent 循环、治理、Tier B 记忆(agentic)、诊断、features 门控。

---

## 1. 心智模型对比

### 现状（研究工具模型）
```
用户必须理解：工作区路径、papers/ 目录、论文库、知识图谱、"项目/进度总结"
UI：ProjectSidebar(路径) | PaperLibrary | GraphWindow | WorkspaceSidebar | PlanBar/progress
Agent 把 .research-agent/ 写进工作区，并自动维护 progress 总结
```

### 目标（个人助手 · opencode/Claude Code 模式）
```
用户工作目录 = 用户自己选的 git 仓库（干净，只有他的文件 + .git），对话与之绑定。
agent 干活 → 改动以文件级 diff 呈现：
    ┌────────────────────────────────────────────┐
    │  ● file_a.py    +12 -3        [同意] [拒绝] │
    │  ● notes.md     +5            [同意] [拒绝] │
    │  [ 同意全部 ]                                │
    └────────────────────────────────────────────┘
  拒绝某文件后可再同意剩余全部；同意落地(commit)。
没有"项目进度总结"——不维护 progress.md，不给用户看内部规划总结。
```

判断标准：**"在哪干活"由用户决定（指定目录）；改动可见可审（diff + 同意/拒绝）；内部检索/记忆/目录结构不暴露。**

---

## 2. 需要删除 / 隐藏的残留（依据代码）

| 类别 | 位置 | 处置 |
|------|------|------|
| 论文库前端 | `PaperLibrary.tsx`, 相关路由 | **删** |
| 知识图谱图 | `GraphWindow`/`ForceGraph` + `/api/graph*` | **删** |
| 上传 PDF / papers 列表 | `/api/upload/pdf`, `/api/papers*`, `/api/workspaces/papers\|paper` | **删** |
| 工作区进度/总结 | `progress.md` 读写、`update_progress`、PlanBar/进度注入 | **删**：不做项目进度总结 |
| 工具/技能管理面板 | `ToolsPanel`, `SkillsPanel` 出用户主 UI | **收进开发者诊断** |
| 启动即建研究目录 | `agent.py` 的 `makedirs(papers/experiments)` | **删** |
| `.research-agent/` 落工作区 | `project_manager` marker/conversations | **移出或最小化**：状态进 git/本地缓存，工作区只留用户文件 |
| 零散工作区路径 UI | `ProjectSidebar` 手动输入 | **收敛为一次设定**：会话开始确定绑定目录 |

> 保留：`/api/workspaces/file(s)` 只读预览（沙箱/工作区内容，供 diff 上下文）。

---

## 3. 全程提案制沙箱 — git-based 逐文件提案

### 原则
- agent 的一切外部副作用（写/改/删文件、shell 中改文件的命令）发生在**绑定工作目录的 git 仓库**内。
- 不自动 commit；每轮/每批后把 `git diff` 按文件分组生成提案。
- 用户交互（opencode/Claude Code 模式）：
  - **[同意全部]** 一键应用所有文件改动；
  - **逐文件**：可先拒绝某些文件，再同意剩余全部；
  - 同意的落地(commit)；被拒的用 `git checkout/restore` 丢弃。
- 细粒度 HITL（逐步 approve 每个工具调用）只保留给危险 shell；普通文件改动不打断。

### 与现状的衔接
- 现有 `git_tool.py`（init/checkpoint）与 `guardrail` 是现成地基：把 "auto checkpoint" 改为 "生成 proposal → 等用户决策"。
- 论文 grep/两阶段落地：论文也作为文件改动走同一提案流（persist 概念不再暴露给用户）。

### 边界
- **不可 git 覆盖的副作用**（联网、安装、启动服务、写仓库外路径）：危险动作仍走 `confirm_required` HITL；普通网络读直接执行但不进 diff。
- 仓库外文件写入：默认禁止（沙箱），白名单例外。

---

## 4. 保留且强化

| 层 | 说明 |
|----|------|
| Agent 循环 | 自研 + function calling；治理/回灌/收敛闸保留 |
| 记忆 | Tier B agentic 读（`search_memory`/`memorize`）+ Tier A 对话；跨会话一致 |
| 诊断 | `feature`/`diagnose` 保留为开发者后台 |
| 配置 | `features` 门控保留；新增能力不进用户 UI |

---

## 5. 系统提示词（需专门一稿）

现 `BASE_SYSTEM_PROMPT` 仍是"研究助手"味（检索论文/写综述/read_paper）。目标一稿：
1. **身份**：通用个人助手；面向用户本人，跨会话了解偏好/领域/历史。
2. **沙箱行为**："你的文件改动以 diff 提案呈现，等用户同意才落地；不要假设已写入。"
3. **记忆**：涉及用户本人主动 `search_memory`；要求记住时 `memorize`。
4. **回复规范**：简洁、给结果、不道歉不套话。
5. **收敛反例**：给足"不要做什么"。

> 提示词单独成稿，不在此展开。

---

## 6. 迁移路线

| 阶段 | 内容 |
|------|------|
| P0 对齐 | 本文档定稿（核心决策已定） |
| P1 内部化 | 删论文库/图谱/上传后端与前端；删 progress 总结；工作目录=git 绑定；去掉零散路径 UI |
| P2 沙箱 | git-based proposal；`/api/proposals`(list/apply/reject)；UI 逐文件同意/拒绝 + 同意全部 |
| P3 前端收敛 | 主界面=对话 + diff 提案流；Tools/Graph/Paper/Workspace 面板移诊断或移除 |
| P4 记忆&Prompt | prompt 个人助手一稿；记忆与工作区策略校准 |
| P5 回归 | 全量测试 + 手工走查 |

---

## 7. 已定决策

- [x] 产品定位 = 通用个人助手（研究外壳删除，arXiv 等退化为普通工具）
- [x] sandbox = git-based（工作目录即 git 仓库）
- [x] 工作目录：用户指定并绑定；未指定落用户默认目录；不做隐藏 data_dir 目录
- [x] 对话与当前工作目录绑定
- [x] 不做工作区/项目进度总结功能（删 progress 机制）
- [x] 交互 = opencode/Claude Code 逐文件提案：同意全部，或先拒绝部分文件再同意剩余

---

## 8. 执行隔离与工作区回滚（硬化）

命令执行的威胁模型分两层，必须分开解决：

- **能碰哪里**（隔离）：`sandbox.py` 把 `shell_exec` 放进一次性容器。
- **改了能不能退**（回滚）：`checkpoint.py` 在执行前对工作区做 git 快照。

### 8.1 沙箱后端（`shell.backend`）

| 取值 | 行为 |
|------|------|
| `auto`（默认） | 有 docker 用 docker，否则降级 local 并告警 |
| `local` | 始终本机执行（无隔离，仅 guardrail + HITL） |
| `docker` | 始终容器；docker 不可用则**直接失败**，不静默降级 |

`docker` 容器参数：只挂载工作区到 `/work`、`--network none`（默认断网）、
`--memory/--cpus/--pids-limit` 资源上限、`--read-only` rootfs + `--tmpfs /tmp`、
`no-new-privileges`、`--rm`。后台任务同样走同一后端。

> 注意：工作区是 **bind mount**，所以容器保护的是**工作区之外**的本机；
> 工作区内的写入仍直接落宿主 —— 这正是需要 checkpoint 的原因。

### 8.2 工作区检查点（`shell.checkpoint`，默认开）

执行 `shell_exec` 前，用临时索引（`GIT_INDEX_FILE`）把整个工作树
（含未跟踪、未忽略的文件）写成一个 commit 对象，存到
`refs/research-agent/checkpoints/*`，**不改动工作树与真实索引**。
污染后 `restore_checkpoint()` = `checkout <sha> -- .` + `clean -fd` + `reset`，
即可回到执行前状态（保留执行前未提交的 agent 编辑）。

**已知边界**（诚实口径）：
- 仅支持 git 工作区；非 repo 不做快照，无回滚。
- git-ignored 文件不进快照（`git add -A` 遵守 .gitignore），其覆盖不可恢复。
- `restore_checkpoint()` 会 `clean -fd` 删除未跟踪文件，属于显式用户动作，不自动触发。
- 单文件级已由 `proposal.py`（keep/undo）覆盖；checkpoint 针对"整条命令的副作用"。

验证：`tests/test_sandbox.py`（9）与 `tests/test_checkpoint.py`（4）确定性覆盖。

