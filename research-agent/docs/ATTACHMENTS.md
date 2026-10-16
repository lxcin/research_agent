# 附件：上传 / 暂存 / 解析（attachments）

> 让 Agent 能接收**用户真实材料**（代码、文本、PDF、Office、图片）并基于它们接地作答。
> 代码：`src/research_agent/attachments.py` · 测试：`tests/test_attachments.py`

## 1. 定位与意义
- 把 Agent 输入从"只能读工作区文本"扩展到"能接收文件/图片"，**基于真实数据回答而非猜测**。
- 与"截屏问答"共用同一条底层：**附件 → 内容块(text | image_url) → 注入本轮 user 消息**。截屏只是后续可加的另一个"生产者"。
- 严守项目边界：**工作区外暂存、不自动写工作区、不入个人长期记忆**。

## 2. 暂存到哪（chat 级）
```
data_dir/attachments/<chat_id>/<id>/
  meta.json        # id/chat_id/filename/ext/kind/size/sha256/created/original/parsed
  original.<ext>   # 原始附件
```
- **作用域 = 会话（chat）**：随会话使用；`research-agent attachments cleanup [--chat]` 回收。
- 工作区之外、不进 git、不进提案；`sha256` 在会话内去重。
- 默认**不写日志正文**、不执行附件内容。

## 3. 解析（确定性，按类型）

| 类型 | 产出 | 依赖 / 降级 |
|---|---|---|
| 文本/代码（.py/.md/.json/.csv/…） | **text 块**（编码探测 + 截断） | 内置 |
| PDF | **text 块**（复用 `network._extract_pdf`） | `pypdf`；缺→明确报错；扫描件无文本层→报错 |
| 图片（.png/.jpg/.webp/.gif/.bmp） | **image_url 块**（默认压缩） | `Pillow`；缺→发原图（不静默失败） |
| Office：.docx / .xlsx / .pptx | **text 块** | `python-docx` / `openpyxl` / `python-pptx` |
| 其他 | 拒绝 | — |

- 图片：`thumbnail(长边≤image_max_side)` + JPEG 质量 `image_quality`，控 token/费用。
- 文本/解析结果截断到 `max_chars`（模型据此知道不完整）。

## 4. 注入方式（多模态）
- 附件作为**本轮 user 消息的 content 块**：`[{"type":"text","text":<问题>}, <blocks>]`（`context.build_context`）。
- 因此**无需路由判断**——用户"附了"就意味着"这一问要用它"。
- `context.count_tokens/trim_messages` 已兼容块内容（图片计为 `[image]`，不破坏结构）。
- 模型需支持视觉（如 DeepSeek V4.1-Flash `input_modalities` 含 `image`）。

## 5. 记忆 / 隐私策略
- **默认不入 Tier B 记忆**、不落工作区（原始附件是易逝的**工作材料**）。
- 仅当用户**显式要求**（"把结论记下来"）→ 走 `memorize`，存**结论文本**，不存原图。
- 要长期留档 → 由 Agent 用 `file_write` 走**文件提案**写入工作区。

## 6. 用法
```bash
# 交互式：启动即附加 / 会话内附加
research-agent chat --attach ./train.py --attach ./error.png
# 会话内：
/attach ./report.pdf

# 附件管理（chat 级暂存）
research-agent attachments list
research-agent attachments cleanup [--chat default]
```

## 7. 配置（`config.yml`）
```yaml
attachments:
  max_mb: 20               # 单文件大小上限
  image_max_side: 1280     # 图片长边压缩上限
  image_quality: 80
  max_chars: 8000          # 解析文本返回上限
  pdf_max_pages: 50
```

## 8. 依赖
```bash
pip install -e ".[attach]"   # Pillow + pypdf + python-docx + openpyxl + python-pptx
```
缺失时：文本仍可用；PDF/Office/图片压缩按上表**明确报错或降级**。

## 9. 已知边界
- 不做事后 OCR（扫描件 PDF 无文本层 → 报错）。
- 图片直接给视觉模型，不做文字提取；成本随图片大小/PDF 页数上升（`telemetry` 会度量）。
- 暂存为 chat 级：跨会话不复用；`cleanup` 回收。
- 截屏问答为后续（复用同一管道）。
