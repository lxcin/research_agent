# 联网插件（web）— 行为、抓取内容与安全边界

> 插件 id：`web` · 工具：`web_fetch` / `web_search` · 默认**关闭**
> 代码：`src/research_agent/tools/builtin/network.py` · 测试：`tests/test_network_tools.py`

联网插件给 Agent 增加**只读的出站 HTTP 能力**：抓取网页正文、通过搜索服务商检索。
它是与 shell 沙箱（`shell.network: false`）**相互独立**的显式出站通道，必须显式开启。

```bash
research-agent plugin enable web      # 开启（持久化到 config.yml 的 plugins.web.enabled）
research-agent plugin disable web     # 关闭，运行时立即注销工具
research-agent plugin list            # 查看状态
```

未开启时，`web_fetch` / `web_search` 不出现在模型的工具列表里，Agent 完全不知道它们存在。

---

## 1. 工具行为

### 1.1 `web_fetch(url)`

抓取一个 URL 并返回**可读正文文本**。处理流程：

```
url
 ├─ 1. 协议检查：只允许 http / https（file/ftp/... 直接拒绝）
 ├─ 2. 域名策略：deny_domains 命中 → 拒绝；allow_domains 非空且未命中 → 拒绝
 ├─ 3. SSRF 检查：DNS 解析该域名，任一 IP 属于私网/回环/链路本地/保留 → 拒绝
 ├─ 4. 发起请求（User-Agent 固定；不发送 Cookie/Authorization；不跟随自动重定向）
 │     ├─ 若 3xx 且带 Location：join 出下一跳 → 回到步骤 1（每跳重新校验），
 │     │   超过 max_redirects 跳 → 停止
 │     └─ 逐块读取响应体，累计超过 max_bytes → 截断
 ├─ 5. 状态码 >= 400 → 失败
 ├─ 6. 按 Content-Type 分支：
 │     ├─ text/html / application/xhtml+xml → 提取可读正文
 │     ├─ text/*、application/json、application/xml、*+json → 原样文本
 │     └─ 其他（PDF/图片/二进制/压缩包...）→ 拒绝，不返回内容
 └─ 7. 正文裁到 max_chars，返回 JSON 字段给模型
```

返回给模型的字段（`ToolResult.data`）：

| 字段 | 含义 |
|------|------|
| `url` | 最终实际请求的 URL（经过重定向后的） |
| `status` | HTTP 状态码 |
| `content_type` | 响应的 Content-Type |
| `source` | `html`（提取后的正文）或 `text`（原样文本） |
| `content` | 正文文本，≤ `web.max_chars` |
| `chars` / `bytes` | 返回字符数 / 实际读取字节数 |
| `truncated` | 是否因 `max_bytes` 或 `max_chars` 被截断 |

### 1.2 `web_search(query, max_results?)`

用配置的搜索服务商检索，返回 `{title, url, snippet}` 列表。

| provider | 说明 | 需要的 key |
|----------|------|-----------|
| `none`（默认） | 不搜索，调用直接返回"未配置"错误 | — |
| `exa` | POST `api.exa.ai/search`，AI 语义搜索（借鉴 Agent-Reach 的选型） | `web.exa_api_key_env`（默认 `EXA_API_KEY`） |
| `tavily` | POST `api.tavily.com/search` | `web.search_api_key_env`（默认 `TAVILY_API_KEY`） |
| `serper` | POST `google.serper.dev/search` | 同上 |
| `duckduckgo` | GET `html.duckduckgo.com/html/`，解析结果链接（免 key，稳定性/ToS 有风险，best-effort） | — |

**多后端有序路由（借鉴 Agent-Reach"首选+备选"）**：配置 `web.search_providers: [exa, tavily, duckduckgo]`，
按序尝试，**第一个成功即返回**（结果带 `backend` 字段）；某后端缺 key/报错则降级到下一个，全部失败才报
`Search failed on all backends: ...`（含每个后端的原因）。只配 `web.search_provider`（单后端）时行为兼容旧版。

`research-agent web-doctor` 按路由顺序体检各后端（是否就绪/缺哪个 key），类似 `agent-reach doctor`。

`web_search` 只返回搜索结果列表，**不自动抓取**结果页；如需正文，由模型再调用 `web_fetch`。

### 1.3 抓取后端路由（direct + Jina Reader）

借鉴 Agent-Reach 的"读网页首选"：`web.fetch_backends: [direct, jina]` 有序尝试，第一个成功即返回（结果带 `backend`）。

- **`direct`**：本机 `httpx` 直连 + 本地提取（HTML/PDF/JSON/文本，见 §2）。
- **`jina`**：经 **Jina Reader**（`https://r.jina.ai/<url>`，免费无 Key）把任意 URL 转成干净 Markdown——
  **专治 direct 抓不到的 JS 重页面**（此前 GitHub 只能拿到导航样板）。
- **降级条件**：`direct` 请求失败 / 返回非 2xx / 正文类型且提取长度 `< web.fetch_min_chars`（默认 200）→ 自动走下一个后端；全部失败报 `Fetch failed on all backends: …`。
- **安全**：降级到 Jina 前会先对**目标 URL** 跑一遍 SSRF/协议校验（不把内网地址交给第三方）；但 Jina 是服务端抓取，IP 级 SSRF 对本机请求有效、对目标仅尽力而为。

### 1.4 本地 dev 服务（回环直连，参考 opencode 的本地 webfetch）

`web.allow_localhost: true` 时（默认关闭）：
- 解析结果**全部是回环**（`localhost` / `127.0.0.1` / `::1`）的 URL → **强制走 `direct`**（外部读取器够不到你本机端口），并绕过 SSRF 私网拦截；
- **只放行回环**：`10/8`、`192.168/16`、`172.16/12` 等其他私网**仍然拦截**（比 `allow_private: true` 安全得多）；
- 用于抓本地正在跑的 dev 服务（如 `http://127.0.0.1:3000/`），等价于 opencode 里直接 `curl localhost:PORT`。

---

## 2. 一次 `web_fetch` 到底抓取了什么

### 2.1 发出去的请求

- 方法：`GET`
- 请求头：固定 `User-Agent`（可配 `web.user_agent`）+ `Accept: */*`
- **不发送**：Cookie、Authorization、Referer、任何本地凭据
- 每个域名**不做登录**，抓取的是公开可匿名访问的内容

### 2.2 HTML 页面提取出的内容

当响应是 HTML 时，返回的是**去标签后的可见正文文本**，具体处理：

1. 删除整块 `<script>`、`<style>`、`<noscript>`、`<template>`、`<svg>`、`<head>`；
2. `<br>` 和块级标签结尾（`</p>` `</div>` `</li>` `</h1..h6>` `</tr>` `</article>` `</section>`）转换为换行；
3. 去除所有剩余 HTML 标签；
4. 反转义 HTML 实体（`&amp;` → `&`，`&#39;` → `'` …）；
5. 折叠连续空白/空行。

因此返回内容包含：**标题、段落、列表、表格文字等页面上可见的文字**。
不包含/不保留：HTML 标签结构、CSS、JS、图片二进制、`<meta>`、隐藏元素以外的语义信息、导航图标的可点击结构（除非它们是文字）。

若安装了可选依赖 `trafilatura`（`pip install -e ".[web]"`），会优先用它做正文抽取（自动去导航/页脚/广告），质量更好；不可用时回退到上面的内置正则清洗，**绝不因为缺依赖而报错**。

### 2.3 非 HTML 响应

- JSON / XML / 纯文本：**原样返回**（含 JSON 结构），适合直接调用公开 API（如 arXiv、GitHub）；
- **PDF：解析文本层后返回**（`source="pdf"`）。用可选依赖 `pypdf`（`pip install -e ".[web]"`）按页提取 `extract_text()`，最多 `pdf_max_pages` 页，页间空行分隔；`max_pdf_bytes` 单独放宽读取上限。扫描件/纯图片 PDF 没有文本层 → 明确报错，不做 OCR。未安装 `pypdf` → 明确提示安装，不静默失败。Content-Type 缺失但响应体以 `%PDF-` 开头时也能识别。
- 图片、ZIP、音视频等其余二进制：**拒绝**，返回 `Unsupported content-type ...`，不把二进制塞进上下文；
- 含 `charset` 时按其解码，解码失败按 UTF-8 → latin-1 顺序兜底。

### 2.4 截断

| 配置 | 控制对象 | 超限行为 |
|------|----------|----------|
| `web.max_bytes` | 读取的**原始响应字节** | 停止读取，`truncated=true` |
| `web.max_chars` | 交给模型的**正文字符数** | 截断到上限，`truncated=true` |

模型看到的是 `content`，并会看到 `truncated` 标记，从而知道内容不完整。

---

## 3. 安全边界

这是本项目其它安全叙事（guardrail / HITL / path sandbox）之外**新增的一条出站攻击面**，因此做了如下约束：

| 机制 | 行为 |
|------|------|
| **默认关闭** | 插件 `enabled=False`，不开启则工具不存在 |
| **协议白名单** | 仅 `http`/`https`，阻断 `file://`、`gopher://`、`ftp://` 等 |
| **SSRF 防护** | DNS 解析后逐个 IP 检查并拦截：IPv4 私网/回环/链路本地/保留/组播/未指定（含云元数据 `169.254.169.254`）；IPv6 回环 `::1`、链路本地 `fe80::/10`、ULA `fc00::/7`、组播 `ff00::/8`、文档段 `2001:db8::/32` 等。**不**把整个 IANA `2001::/23`（含 Teredo）当私网，避免对特殊用途地址误报 |
| **转换地址解包** | `::ffff:a.b.c.d`（IPv4-mapped）、`2002::/16`（6to4）、`64:ff9b::/96`（NAT64）会**还原出内嵌 IPv4 再做检查**，防止用转换地址把 `127.0.0.1`/`10.x` 走私进来绕过 |
| **域名 allow/deny** | `web.allow_domains` 非空即白名单；`web.deny_domains` 始终拒绝；按域名及子域匹配 |
| **重定向逐跳校验** | 关闭自动跟随，手动跳转且**每一跳都重跑步骤 1–3**，防止"公网 URL 302 到内网"的绕过 |
| **资源上限** | `timeout` 超时、`max_bytes` 字节上限、`max_chars` 字符上限、`max_redirects` 跳数上限 |
| **只读、无工作区副作用** | 工具 `side_effect=False`：不写工作区、不改文件，**不进 keep/undo 提案流**，无需 HITL 审批 |
| **无凭据外泄** | 不读取/不发送本地凭据，不写日志正文 |

### 残余风险（已知限制）

- **DNS rebinding（TOCTOU）**：校验时解析一次，httpx 实际连接时会再解析一次，理论上存在被操控的解析器返回不同 IP 的时间窗。本项目为个人/本地使用场景，未做 IP 固定（pinning）；高安全场景应改用固定 IP 连接 + Host 头 + 自定义 SNI。
- **端口未限制**：允许任意端口（如 `:8080`），仅受 IP 策略保护。
- **不校验 TLS 证书自定义/企业代理**，使用 httpx 默认校验。
- **提示注入（prompt injection）**：抓回的网页正文会进入模型上下文，恶意页面可能包含"忽略以上指令"之类内容。内容本身不可信，模型需按数据而非指令对待。
- **robots.txt / 访问频率 / 版权**：未做 robots 检查、未做速率限制、未做缓存；请遵守目标站点的 ToS 与版权。
- **无认证抓取**：无法抓取需要登录的页面。

---

## 4. 抓取内容的去向与隐私

- 抓取结果作为**本回合的工具结果**进入 LLM 上下文（`runtime.py` 会把 `ToolResult.data` 序列化给模型）。
- 按 V4 记忆设计，**工具调用/工具结果不写入 Tier B 长期记忆**（`memory/source.py` 显式过滤）；但模型在最终答复里引用网页内容时，该答复会随对话历史持久化。
- 诊断事件流（`diagnostics/recorder.py`）只记录 `tool` 事件的 `web_fetch`/`web_search`、URL、状态、字符数等元数据，**不记录抓取的正文**。

---

## 5. 配置参考（`~/research-agent-data/config.yml`）

```yaml
web:
  timeout: 15              # 单次请求超时秒
  max_bytes: 2000000       # 普通响应体读取上限
  max_pdf_bytes: 20000000  # PDF 响应体读取上限（PDF 通常更大）
  pdf_max_pages: 50        # PDF 最多解析页数
  max_chars: 8000          # 返回正文上限
  fetch_backends: [direct, jina]  # 有序抓取后端；jina=r.jina.ai 读 JS 重页面
  jina_reader_base: https://r.jina.ai/
  fetch_min_chars: 200     # direct 正文短于此值时降级
  max_redirects: 5
  allow_private: false     # true 会关闭 SSRF 防护（危险，仅本地调试）
  allow_localhost: false   # true = 仅放行回环(localhost/127.0.0.1/::1)，用于直连本地 dev 服务
  allow_domains: []        # 非空 = 白名单，如 ["arxiv.org", "github.com"]
  deny_domains: []         # 始终拒绝，如 ["internal.corp"]
  search_provider: none    # 单后端（兼容旧配置）none / exa / tavily / serper / duckduckgo
  search_providers: []     # 有序多后端，按序探测第一个可用即用，如 [exa, tavily, duckduckgo]
  search_api_key_env: TAVILY_API_KEY
  exa_api_key_env: EXA_API_KEY
  search_max_results: 5
```

---

## 6. 测试覆盖（离线、Mock，无真实网络）

`tests/test_network_tools.py`：

- 协议拦截：`ftp`/`file`/空 URL；
- SSRF：回环 `127.0.0.1`、云元数据 `169.254.169.254`、私网 `10/192.168/172.16`、IPv6 `::1`、ULA/链路本地/组播/文档段；IPv4-mapped、6to4、NAT64 解包后判私网；`2001::1` 等特殊用途**放行**；主机解析出「公网+私网」混合地址时仍拦截；
- 域名 allow/deny 匹配（含子域）；
- HTML 清洗：删除 script/style，保留中文与实体反转义；
- `web_fetch`：HTML 正文提取、JSON 原样返回、二进制（图片）拒绝、字节截断、404、**重定向到内网被拦截且不发起第二跳请求**；
- 抓取后端路由：direct 正文过短时降级到 Jina、仅 Jina 后端、Jina 对私网目标先拦截、全部后端失败聚合；
- PDF：正常解析多页文本、无 Content-Type 时按 `%PDF-` 嗅探、缺 `pypdf` 明确报错、扫描件无文本报错、损坏 PDF 报错、页数上限、单页解析异常跳过；
- `web_search`：未配置 provider、DuckDuckGo 链接解析、Tavily 缺 key、Tavily 正常解析；
- 插件生命周期：默认关闭 → 启用后工具注册、`side_effect=False`、无审批。

所有 HTTP 均通过 monkeypatch 假客户端，CI 不产生网络请求。

---

## 7. 与 shell 沙箱的关系

- shell 沙箱（`shell.backend=docker`）默认 `--network none`，容器内无法联网；
- 联网插件是**独立于 shell 的** Python 进程内出站，不经过沙箱；
- 因此二者是"关沙箱的网络"与"开插件的网络"两条正交通道，开启插件即显式放开 Agent 的出站读取能力。

## 8. 网络依赖与失败处理

- **抓取来源 = 本机网络出口**。`web_fetch`/`web_search` 用本机进程的 `httpx` 直接出站（默认 `trust_env=True`，会遵循 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`）。它**不经过 shell 沙箱**，也不自带任何网络加速/代理/镜像是本机网络能到哪就抓到哪。
- **因此失败往往不是工具问题，而是环境**：DNS 污染（解析到无关 IP）、区域封锁、连接超时、站点反爬/限流等。
- **告知 Agent 的约束**（已写进工具描述，随 `generate_capabilities()` 注入系统提示）：`web_fetch`/`web_search` 依赖本机网络、可能失败；**同一 URL/查询失败后不要反复重试**，应改用其他 URL/来源，或直接向用户说明抓取失败及原因。
- 诊断事件流会记录每次 `web_fetch`/`web_search` 的 URL 与失败错误，便于事后判断是"环境封锁"还是"代码问题"。

---

## 9. 后续可扩展（当前未实现）

`http_request`（POST/PUT 等写请求，需接 HITL 审批）、robots.txt 遵从、速率限制与缓存、认证头注入、代理支持、IP 固定防 DNS rebinding、搜索结果页自动抓取。
