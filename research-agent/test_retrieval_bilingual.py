"""Bilingual retrieval test — fix Chinese Precision@1 gap."""
from rank_bm25 import BM25Okapi
import jieba

# Mixed Chinese-English corpus (simulating real KB)
docs = [
    {"id": "c1", "text": "Transformer多头自注意力机制 attention mechanism scaled dot-product. 位置编码positional encoding. 编码器解码器encoder-decoder序列到序列sequence-to-sequence."},
    {"id": "c2", "text": "BERT掩码语言建模 masked language modeling 双向预训练bidirectional pre-training. Transformer编码器encoder深层上下文表示deep contextual representations. 微调fine-tuning分类classification问答QA."},
    {"id": "c3", "text": "GPT-3 1750亿参数 autoregressive language model 自回归语言模型. 少样本学习few-shot learning 无需微调without fine-tuning. 上下文学习in-context learning 文本提示text prompts."},
    {"id": "c4", "text": "Vision Transformer图像块image patches作为tokens. 标准Transformer编码器encoder处理. 大规模数据集large datasets预训练pre-training. 图像分类image classification CNN性能competitive."},
    {"id": "c5", "text": "CLIP对比学习contrastive learning 图像文本联合嵌入joint image-text embeddings. 零样本迁移zero-shot transfer 下游视觉任务downstream vision tasks."},
]

corpus = [list(jieba.cut(d["text"])) for d in docs]
bm25 = BM25Okapi(corpus)

def single_query(query):
    """Pure Chinese or English query."""
    tokens = list(jieba.cut(query))
    scores = bm25.get_scores(tokens)
    return docs[scores.argmax()]["id"]

def bilingual_query(query_cn, query_en=None):
    """Merge Chinese + English tokens for cross-lingual retrieval."""
    tokens = list(jieba.cut(query_cn))
    if query_en:
        tokens += list(jieba.cut(query_en))
    else:
        lowercase = query_cn.lower()
        en_words = [w for w in lowercase.split() if all(ord(c) < 128 for c in w)]
        tokens += en_words
    scores = bm25.get_scores(tokens)
    return docs[scores.argmax()]["id"]

queries = [
    ("图像分类模型", "image classification model", "c4"),
    ("对比学习 图像 文本", "contrastive learning image text", "c5"),
    ("少样本学习 语言模型", "few-shot learning language model", "c3"),
    ("多头注意力", "multi-head attention", "c1"),
    ("双向编码器 预训练", "bidirectional encoder pre-training", "c2"),
    ("零样本迁移 视觉", "zero-shot transfer vision", "c5"),
    ("掩码语言模型", "masked language model", "c2"),
    ("自回归 文本生成", "autoregressive text generation", "c3"),
]

print("=" * 60)
print("Bilingual Retrieval: Chinese -> Chinese + English")
print("=" * 60)

single_correct = 0
bilingual_correct = 0
for cn, en, expected in queries:
    s = single_query(cn)
    b = bilingual_query(cn, en)
    if s == expected: single_correct += 1
    if b == expected: bilingual_correct += 1
    s_label = "OK" if s == expected else f"->{s}"
    b_label = "OK" if b == expected else f"->{b}"
    print(f"  CN: {cn:20s} | 单语: {s_label:4s} | 双语: {b_label:4s} | expected: {expected}")

print()
print(f"  Single-language Precision@1: {single_correct}/{len(queries)} = {single_correct/len(queries):.0%}")
print(f"  Bilingual   Precision@1: {bilingual_correct}/{len(queries)} = {bilingual_correct/len(queries):.0%}")
