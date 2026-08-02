"""BM25 retrieval quality test — no ChromaDB needed."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from rank_bm25 import BM25Okapi
import jieba

docs = [
    {"id": "p1", "text": "The Transformer uses multi-head self-attention and scaled dot-product attention. Positional encodings are added to input embeddings. The encoder-decoder architecture enables translation tasks."},
    {"id": "p2", "text": "BERT uses masked language modeling for bidirectional pre-training. It leverages the Transformer encoder for deep contextual representations."},
    {"id": "p3", "text": "GPT-3 is a 175B parameter autoregressive language model capable of few-shot learning without fine-tuning. In-context learning via text prompts."},
    {"id": "p4", "text": "Vision Transformer treats image patches as tokens and processes them with a standard Transformer encoder. Pre-training on large datasets yields competitive image classification."},
    {"id": "p5", "text": "CLIP learns joint image-text embeddings via contrastive learning. It enables zero-shot transfer to downstream vision-language tasks."},
]

corpus_tokens = [list(jieba.cut(d["text"])) for d in docs]
bm25 = BM25Okapi(corpus_tokens)

print("=" * 60)
print("BM25 Retrieval Quality Test")
print("=" * 60)

queries = [
    ("attention mechanism", "p1"),
    ("language model few-shot", "p3"),
    ("image classification", "p4"),
    ("quantum computing physics", None),
    ("bidirectional encoder pre-training", "p2"),
    ("contrastive learning image text", "p5"),
    ("self-attention multi-head", "p1"),
    ("zero-shot transfer", "p5"),
]

correct = 0
total = len(queries)
for query, expected_id in queries:
    scores = bm25.get_scores(list(jieba.cut(query)))
    top_idx = scores.argmax()
    top_id = docs[top_idx]["id"]
    hit = top_id == expected_id
    if hit:
        correct += 1
    label = "PASS" if hit else "MISS"
    expected_str = expected_id or "-"
    print(f"  [{label}] {query:35s} -> {top_id} (expected {expected_str})")

print()
print(f"  Precision@1: {correct}/{total} = {correct/total:.0%}")

# Rank test: does each paper rank itself #1 for its own title?
print()
print("--- Self-retrieval (each paper's title) ---")
self_queries = [
    ("attention is all you need transformer", 0),
    ("bidirectional encoder representations", 1),
    ("gpt language model few-shot", 2),
    ("vision transformer image patches", 3),
    ("clip contrastive language image", 4),
]
self_correct = 0
for query, expected_idx in self_queries:
    scores = bm25.get_scores(list(jieba.cut(query)))
    top_idx = scores.argmax()
    found = docs[top_idx]["id"]
    if top_idx == expected_idx:
        self_correct += 1
    print(f"  [{found}] {query}")
print(f"  Self-retrieval: {self_correct}/{len(self_queries)} = {self_correct/len(self_queries):.0%}")
