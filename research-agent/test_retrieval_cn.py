"""BM25 retrieval quality test — Chinese queries."""
from rank_bm25 import BM25Okapi
import jieba

docs_cn = [
    {"id": "c1", "text": "Transformer使用多头自注意力机制和缩放点积注意力。位置编码被添加到输入嵌入中。编码器-解码器架构支持序列到序列任务。"},
    {"id": "c2", "text": "BERT使用掩码语言建模进行双向预训练。它利用Transformer编码器获取深层上下文表示。通过微调适应下游任务如分类和问答。"},
    {"id": "c3", "text": "GPT-3是一个1750亿参数的自回归语言模型。具备无需微调的少样本学习能力。通过文本提示实现上下文学习。"},
    {"id": "c4", "text": "Vision Transformer将图像块视为token，用标准Transformer编码器处理。在大规模数据集上预训练后，图像分类性能与CNN相当。"},
    {"id": "c5", "text": "CLIP通过对比学习学习图像和文本的联合嵌入空间。实现了对下游视觉任务的零样本迁移。"},
]

corpus_cn = [list(jieba.cut(d["text"])) for d in docs_cn]
bm25_cn = BM25Okapi(corpus_cn)

print("=" * 60)
print("BM25 Chinese Retrieval Test")
print("=" * 60)

queries = [
    ("注意力机制", "c1"),
    ("图像分类模型", "c4"),
    ("少样本学习", "c3"),
    ("和弦理论 物理学", None),
    ("双向编码器", "c2"),
    ("对比学习 图像 文本", "c5"),
    ("自注意力 多头", "c1"),
    ("零样本迁移", "c5"),
]

correct = 0
for query, expected in queries:
    scores = bm25_cn.get_scores(list(jieba.cut(query)))
    top_idx = scores.argmax()
    top_id = docs_cn[top_idx]["id"]
    hit = top_id == expected
    if hit:
        correct += 1
    label = "PASS" if hit else "MISS"
    expected_str = expected or "-"
    print(f"  [{label}] {query:20s} -> {top_id} (expected {expected_str})")

print(f"\n  Precision@1 (Chinese): {correct}/{len(queries)} = {correct/len(queries):.0%}")
