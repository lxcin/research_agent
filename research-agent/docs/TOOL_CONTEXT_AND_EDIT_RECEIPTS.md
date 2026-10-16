> 最终收敛（2026-10-16）：不实现索引、哈希缓存或片段放行，也不新增缓存落盘。正常读取在一次流式扫描中得到正文和哈希；交付及回合入口只检查文件元数据，变化即标记过期。编辑时重算哈希，写入前再次复检，不一致拒绝并要求主动读取相关范围。原有工具证据及任务恢复状态继续保存，不属于新增文件缓存。
>
> 三类拒绝分别返回 FILE_CHANGED（文件变化，旧行号可能偏移）、NO_MATCH（原文不存在）和 MULTI_MATCH（原文多处匹配），均明确未写入，并指导检索定位、读取相关范围、重新构造编辑。后两类附 match_count；不默认要求重读整个文件。

# 文件版本、已读范围与上下文管理

更新：2026-10-16。本节替代此前“每次编辑前重新读取”以及“出现下一个工具调用就收缩旧源码”的规则。

## 职责

Kernel 只负责循环。文件工具声明版本、读取范围、编辑和结果投影契约；harness 执行归档、注入前校验、过期通知和批量折叠。Agent 决定读什么、改什么及修改目的，不决定是否压缩上下文。

磁盘是文件当前状态；上下文中的源码只是某次读取的缓存。版本一致、目标已读即可编辑，不要求重复读取。Harness 不自动恢复源码，文件名提及也不会触发自动读取。

## 工具契约 v3

```text
file_read(path, start_line=1, end_line?, start_column=1, limit=8000)
file_edit(path, old_string, new_string, expected_version, intent?)
file_write(path, content, expected_version, intent?)
```

读取输入的 start_line/end_line 为从 1 开始、首尾包含的行号；行以 LF 分隔，保留 CRLF。start_column 为 Unicode 字符列，从 1 开始。对外不再提供字符 offset 输入，传入此旧参数会明确拒绝；内部 offset/next_offset 继续用于准确的已读范围校验。

读取结果包括 version（原始字节 SHA-256）、range_hash（返回片段 UTF-8 字节哈希）、encoding、bytes、total_lines、content、实际 range、read_coverage、complete、has_more、next_cursor、truncated_lines。

- range / read_coverage 的 start 包含、end 不包含，均用 line/column 表达，不把未展示的间隙当成已读。
- 普通页面优先在完整行后结束；超长行只返回部分，明确 truncated_lines，并以 next_cursor.line/column 续读。
- next_cursor 的 line/column 分别传给下次 start_line/start_column。没有自动续读。
- complete 表示请求范围内的可用内容未因页上限截断；has_more 表示当前片段之后文件还有内容，二者含义不同。
- 默认 8000、最大 16000 字符；为避免拆开 CRLF，边界处最多额外携带一个 LF。

## 读取与资源边界

支持最大 64 MiB 的 UTF-8 / UTF-8 BOM 文本。每次从同一个句柄以固定块扫描，增量解码并计算完整版本，正文缓冲有界，不把整文件载入内存。巨型单行也不会形成无限增长的行缓冲。

NUL 内容拒绝文本读取；UTF-8 严格解码失败返回 UNSUPPORTED_ENCODING。返回可获取的大小和字节哈希，不产生文本编辑资格。NUL 检测不是通用二进制格式识别器，不尝试猜编码后静默替换字符。

扫描前后核对文件身份、大小和时间；发现变化返回 FILE_CHANGED_DURING_READ。Windows 不比较 fstat/stat 语义不一致的 ctime。此检查是辅助防护，不保证检测所有并发写入；编辑前仍验证实际内容哈希。

首版不做哈希缓存或行偏移索引，每次分页仍扫描整文件。宿主交付读取结果时只核对读取时记录的元数据，不再次扫描正文。元数据探测可能漏掉保留时间等改写，编辑时的实际内容哈希校验继续兜底。

file_grep 的文件大小范围同步到 64 MiB。Python 回退按有界片段搜索，长行的跨片段正则可能漏匹配；返回定位片段不是文本编辑的读取资格，仍需 file_read。符号索引和语义定位不在本批范围内。

## 版本与覆盖范围

filesystem 插件注入持久化会话字段 filesystem.views，按会话、工作区和规范化文件路径隔离。保存当前读取/写出版本、最近探测版本、已读范围与 stale；范围按版本合并，版本变化后不沿用旧范围。组件随宿主会话状态和执行检查点保存、恢复，插件卸载通过原组件生命周期移除运行时字段。

编辑已有文件必须：

1. 显式携带 expected_version，且与磁盘当前原始字节哈希一致。
2. 有本会话对此版本的有效读取/写出记录。
3. 精确替换的 old_string 唯一匹配，且实际匹配区间完全包含在已读范围中。
4. 整文件覆盖具备完整文件的读取覆盖；局部读取不能授权整文件覆盖。

新建文件显式使用 expected_version=absent；已有文件不能用 absent 覆盖。创建采用排他性发布，竞争者先创建则拒绝。

失败返回 VERSION_REQUIRED、FILE_CHANGED、NOT_READ、NO_MATCH 或 MULTI_MATCH 等结构化原因，completed=false。不自动重试，由 Agent 按需重新读取、重新规划。

range_hash 记录为证据，不用于放宽写入条件。文件别处变化也拒绝；不自动执行片段级合并。

成功编辑更新文件版本，旧版本读取不再具备编辑资格。精确编辑只把明确写出的替换片段登记到新版本；其他位置按需重读，不猜测复杂行号映射。整文件写出登记完整已知内容。无操作编辑保留原版本和原覆盖范围。

如果宿主因为工具结果预算仅交付了预览，该读取不能授权整个返回范围；当前实现保守清空此版本覆盖，并提示缩小读取范围，不凭预览编辑。

## 过期通知与上下文

新用户回合与执行恢复入口，harness 调用工具声明的 context_probe，通过文件身份、大小和时间等元数据探测本会话已跟踪文件。变化、删除或无法读取时标记过期，只追加合并限长通知，不读取新源码，也不注入旧源码。

恢复入口如果收到过期通知，作为固定边界批量折叠已完成的旧读取正文。已完成读取的结果回放在交付前再次核对元数据；失效则只交付历史元数据、过期标记和主动读取提示。

正常轮内的成功编辑、再读取不触发历史物理收缩；编辑回执通过末尾返回表达旧读取已过期。已完成原始参数及完整结果仍独立归档，待执行参数不改变。

当输入用量超过 soft_watermark（默认可用输入空间的 0.8）时，宿主尝试一次批量折叠；至少节省 min_fold_tokens（默认 512）才提交，超过硬预算时允许更小的有效节省。折叠后的引用和摘要确定性渲染，重复处理不改字节；发出 context_fold 事件记录节省量。硬预算继续由原预算模块约束。

现有宿主每个新用户回合重新组织模型消息，只携带对话结论，不重新注入上一回合的原始工具正文，因此不额外维护跨回合源码正文队列或引入无实际对象的 fold_lag_turns 配置。当前回合完整检查点保留恢复需要的数据；全文归档与模型消息是不同存储层。

## 编辑回执与证据

status 为 success / failed / partial，并返回 completed、verification、path、operation、hash_before/hash_after、version、changed、changes、diff、diff_ref、validation。

先写同目录临时文件、刷盘，临写前重算版本，再替换并读回验证。只有读回字节一致才 completed=true 并发出成功文件事件。无法确认结果时显式报告 partial / unconfirmed；保留“非事务性”边界，不承诺消除编辑器写入与替换之间的竞争窗口。

差异使用带 3 行上下文的 unified diff，明确无末尾换行；模型内联差异最多 8000 字符、范围最多 20 段，超限保留完整 diff_ref 和完整工具结果引用。intent 为可选声明，使用 declared_intent 保存，不等同于已验证的实际影响面。

validation=not_run_by_edit_tool：写入完成不等于语法、测试通过。后者沿用宿主验证机制单独报告。

完整编辑证据、工具原始参数和结果持久保存；“不可丢失”不等于永远全部驻留模型上下文。回滚的起止快照、审阅和冲突数据仍独立管理，不受上下文折叠影响。

工具版本从 2 升级为 3；旧在途调用遇到签名变化继续拒绝盲重放，不由宿主替 Agent 自动补入当前版本号。

## 验证

专项覆盖：版本未变复用、外部改动拒绝、会话与工作区隔离、读取范围间隙、整文件覆盖限制、Unicode/CRLF 长行分页、64 MiB 边界内大文件流式读取、编码拒绝、读期间变更、写前复检、预览不授权编辑、确定性批量折叠、过期读取回放和执行状态恢复。

本批最终测试结果见 artifacts/file-version-focused.xml、artifacts/file-version-boundaries.xml 与 artifacts/file-version-acceptance.xml。


验证结果（2026-10-16）：全量回归 736 passed、8 skipped（artifacts/file-version-acceptance.xml）。全量启动后补齐单独回车字符分页前进、旧 offset 参数显式拒绝两个边界；最终文件读写、上下文、预算、契约与评测调用链专项 100 passed（artifacts/file-version-focused.xml）。此前恢复与回滚边界专项 40 passed；独立验证“写入完成后崩溃，不重复执行或审批”通过。依赖弃用警告不影响结果。
