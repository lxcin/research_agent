"""Comprehensive retrieval quality test — Precision, Recall, NDCG."""
from rank_bm25 import BM25Okapi
import jieba, re, math

# ── 20-paper simulated knowledge base ──
docs = [
    ("p01", "Transformer multi-head self-attention scaled dot-product attention positional encoding encoder-decoder architecture sequence-to-sequence translation. 多头自注意力 缩放点积 位置编码 编码器解码器 序列到序列 翻译"),
    ("p02", "BERT masked language modeling bidirectional pre-training Transformer encoder deep contextual representations. 掩码语言建模 双向预训练 编码器 深层上下文表示"),
    ("p03", "GPT-3 175B autoregressive language model few-shot learning in-context learning prompting zero-shot. 1750亿 自回归语言模型 少样本学习 上下文学习 提示工程 零样本"),
    ("p04", "Vision Transformer ViT image patches tokens standard Transformer encoder classification ImageNet. 图像块 patchtoken 编码器 图像分类"),
    ("p05", "CLIP contrastive learning joint image-text embeddings zero-shot transfer vision-language tasks. 对比学习 图像文本联合嵌入 零样本迁移 视觉语言"),
    ("p06", "DALL-E text-to-image generation discrete VAE Transformer decoder image tokens. 文本到图像生成 离散变分自编码 解码器 图像token"),
    ("p07", "Stable Diffusion latent diffusion model denoising score matching UNet text conditioning high-resolution. 潜在扩散模型 去噪 分数匹配 文本条件 高分辨率"),
    ("p08", "LLaMA open-source large language model efficient training RMSNorm SwiGLU rotary position embeddings. 开源 大语言模型 高效训练 旋转位置编码"),
    ("p09", "LoRA low-rank adaptation fine-tuning large language models parameter-efficient matrix decomposition. 低秩适应 微调 大语言模型 参数高效 矩阵分解"),
    ("p10", "RLHF reinforcement learning from human feedback reward model PPO alignment safety. 强化学习 人类反馈 奖励模型 对齐 安全性"),
    ("p11", "Chinchilla scaling laws optimal model size training tokens compute budget efficiency. 缩放定律 最优模型大小 训练token 计算预算 效率"),
    ("p12", "PaLM pathways language model sparse activation mixture-of-experts TPU training 540B parameters. 路径语言模型 稀疏激活 混合专家 5400亿参数"),
    ("p13", "FlashAttention IO-aware exact attention algorithm tiling kernel fusion GPU memory optimization. IO感知 精确注意力 分片 核融合 GPU内存优化"),
    ("p14", "DPO direct preference optimization offline RL alignment reward-free Bradley-Terry model. 直接偏好优化 离线RL 对齐 无奖励 BradleyTerry模型"),
    ("p15", "RAG retrieval-augmented generation knowledge base external documents hallucination reduction factuality. 检索增强生成 知识库 外部文档 幻觉减少 事实性"),
    ("p16", "PEFT parameter-efficient fine-tuning prompt tuning prefix tuning adapter bottleneck. 参数高效微调 提示调优 前缀调优 适配器 瓶颈"),
    ("p17", "SAM segment anything model promptable segmentation zero-shot generalization image masks. 分割一切模型 可提示分割 零样本泛化 图像掩码"),
    ("p18", "Mamba state space model selective scan linear-time sequence modeling long-range dependency. 状态空间模型 选择性扫描 线性时间 序列建模 长距离依赖"),
    ("p19", "Mixtral mixture of experts 8x7B sparse transformer architecture efficient inference routing. 混合专家 稀疏transformer 高效推理 路由"),
    ("p20", "Gemini multimodal model audio video image text understanding interleaved training. 多模态模型 音频 视频 图像 文本 交错训练"),
]

corpus = [list(jieba.cut(d[1])) for d in docs]
bm25 = BM25Okapi(corpus)

def tokenize(text):
    tokens = list(jieba.cut(text))
    tokens += re.findall(r'[a-zA-Z0-9]+', text)
    return tokens

def search(query, k=5):
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    return [docs[i][0] for i, _ in ranked[:k]], [s for _, s in ranked[:k]]

# ── Test set ──
test_queries = [
    # (query, relevant_ids)  — one or more relevant papers per query
    ("attention mechanism self-attention", ["p01"]),
    ("image generation from text", ["p06", "p07"]),
    ("大型语言模型微调", ["p09", "p16"]),
    ("强化学习 对齐", ["p10", "p14"]),
    ("visual model image segmentation", ["p17"]),
    ("efficient large model training optimization", ["p11", "p08", "p13"]),
    ("检索增强生成 知识库", ["p15"]),
    ("状态空间 序列建模", ["p18"]),
    ("扩散模型 图像生成", ["p07"]),
    ("对比学习 多模态 视觉", ["p05", "p20"]),
    ("tokenization" ,[]),  # no relevant
    ("gravitational wave astronomy", []),
    ("encoder architecture", ["p01", "p02", "p04"]),
    ("parameter efficient fine-tuning adapter", ["p09", "p16"]),
    ("zero-shot transfer learning", ["p05", "p17"]),
]

# ── Metrics ──
def precision_at_k(retrieved, relevant, k):
    ret_k = retrieved[:k]
    hits = sum(1 for r in ret_k if r in relevant)
    return hits / k if k > 0 else 0

def recall_at_k(retrieved, relevant, k):
    ret_k = retrieved[:k]
    hits = sum(1 for r in ret_k if r in relevant)
    return hits / len(relevant) if relevant else 1.0

def ndcg_at_k(retrieved, relevant, k):
    dcg = sum(1.0 / math.log2(i + 2) for i, r in enumerate(retrieved[:k]) if r in relevant)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / idcg if idcg > 0 else 0.0

print("=" * 70)
print("RETRIEVAL QUALITY TEST — 20 papers, 15 queries")
print("=" * 70)

results = []
for query, relevant_ids in test_queries:
    retrieved, scores = search(query, k=5)
    p1 = precision_at_k(retrieved, relevant_ids, 1)
    p3 = precision_at_k(retrieved, relevant_ids, 3)
    p5 = precision_at_k(retrieved, relevant_ids, 5)
    r5 = recall_at_k(retrieved, relevant_ids, 5)
    ndcg = ndcg_at_k(retrieved, relevant_ids, 5)
    results.append((query, len(relevant_ids), p1, p3, p5, r5, ndcg))
    print(f"  Q: {query[:50]:50s} | rel={len(relevant_ids):1d} | P@1={p1:.0%} P@3={p3:.0%} P@5={p5:.0%} R@5={r5:.0%} NDCG={ndcg:.3f}")

print()
avg = {k: sum(r[i] for r in results) / len(results) for i, k in enumerate(["none", "rel", "p1", "p3", "p5", "r5", "ndcg"]) if i > 0}
print(f"  AVERAGE: P@1={avg['p1']:.0%}  P@3={avg['p3']:.0%}  P@5={avg['p5']:.0%}  R@5={avg['r5']:.0%}  NDCG={avg['ndcg']:.3f}")

# ── LLM will call retrieve? ──
print()
print("=" * 70)
print("LLM tool usage prediction for retrieve")
print("=" * 70)
scenarios = [
    ("search arxiv for new attention papers, read one", "search_papers only"),
    ("what did we read about efficient training?", "retrieve → read_paper"),
    ("compare what we know about RL alignment with new arxiv results", "retrieve → search_papers → read_paper"),
    ("find papers about quantum computing", "retrieve → found=0 → search_papers"),
]
for task, expected_path in scenarios:
    print(f"  Input: {task}")
    print(f"  Expected path: {expected_path}")
    print()
