# 文件边界、模型重试与生产计量修复

更新：2026-10-15。

## 文件访问

path_policy.safe_path 统一文件工具和 guardrail 的检查：解析真实路径后按目录归属判断，不再用字符串前缀。工作区 demo 不允许访问同级 demo-other；符号链接与 Windows junction 指向工作区外时拒绝。新建文件检查已存在的父路径，Windows ADS 等含额外冒号的路径拒绝。

glob 禁止绝对路径、驱动器和父目录跳转，通过不跟随目录链接/junction 的枚举器匹配。grep 的 Python 路径使用相同枚举器；rg 接收经过枚举校验的文件列表并显式 --no-follow，参数过长或后端不可用时回退 Python。不将越界匹配结果过滤等同于阻止越界遍历。

这些是访问前的目录检查，不是文件系统事务；不合作进程在校验与打开之间替换目录/链接仍可能形成竞争窗口。没有声称实现操作系统级沙箱。

## 模型失败处理

model_retry 为宿主提供统一策略：超时、连接故障、限流和明确的临时服务错误可重试；鉴权、参数错误及未知错误停止，ModelRequestRejected 穿透内核普通 Exception 捕获，顶层返回 model_error。暂时错误耗尽以 ModelUnavailable 保存 waiting_network，不再消耗思考失败预算；宿主有限续跑仍可能发起额外网络请求。

stream_with_retry 记录已经发布的 reply 文本。发布前的暂时错误可重试；发布后的普通异常转为 PartialResponseError，当前调用不重播。暂时错误由 run_agent 保存部分文本和答复阶段，返回 waiting_network；恢复时只重发答复，reply_reset 通知客户端替换旧前缀。永久错误仍返回 partial_response 并报告原因。取消仍走已有协议，退避等待也可取消；详见[网络恢复](NETWORK_RECOVERY.md)。

默认/兼容流式入口和显式 model_adapter 回答均使用该规则。供应商不支持 stream_options 时，仅明确的 HTTP 400 不支持参数错误允许移除该参数后回退；不会对任意鉴权/服务异常静默再次发送。只有 usage、没有 choices 的流式尾块可正常读取。

## 生产计量

MeteredRuntime 直接包装显式 model_adapter.select，兼容原 call_llm_with_tools 包装；实际路由选中其中一条，不双计。answer 经 llm_usage 事件记账；未上报事件时登记未知用量调用，usage_complete=false、cost_known=false。已有 token 总数表示已观察量，不代表缺失部分为零。

活动运行与回合后 trace 使用 ContextVar，避免两个并发会话覆盖同一全局 collector。可取消调用线程使用已有 copy_context 传播运行上下文。结束后恢复 adapter、回调；没有上下文传播的独立后台线程不隐式借用其他会话的最近 trace，后台任务仍需显式携带自己的归因上下文。

这次没有完成长期记忆纠错、摘要保真、存储容量策略或原生异步资源生命周期，也没有声称完成任意后台任务和所有模型失败费用的精确归因。

## 验收

全量 502 passed / 1 skipped，线程未处理异常按错误处理；8 条第三方依赖弃用警告。新增回归覆盖同名前缀目录越界、Windows junction 越界、递归搜索、发布后失败不重播、暂时故障重试、鉴权错误停止、adapter 选择/回答不重复计数、并发遥测会话隔离。
