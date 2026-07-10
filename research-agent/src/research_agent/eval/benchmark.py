"""Benchmark datasets for RAG evaluation."""

# 3 domains, 3 papers each, 3 ground-truth facts per paper
BENCHMARK_DOMAINS = {
    "attention_mechanism": {
        "query": "attention mechanism in transformers",
        "seed_papers": [
            "Attention Is All You Need",
            "BERT: Pre-training of Deep Bidirectional Transformers",
            "An Image is Worth 16x16 Words: Transformers for Image Recognition",
        ],
        "ground_truth": [
            # Paper 1: Attention Is All You Need
            {"fact": "The Transformer uses scaled dot-product attention", "paper_idx": 0,
             "queries": ["What attention mechanism does the Transformer use?",
                         "How does Transformer compute attention?",
                         "What is scaled dot-product attention?"]},
            {"fact": "Multi-head attention allows the model to jointly attend to information from different representation subspaces", "paper_idx": 0,
             "queries": ["Why use multi-head attention?", "What is the benefit of multi-head attention?",
                         "How does multi-head attention work?"]},
            {"fact": "The Transformer achieves 28.4 BLEU on WMT 2014 English-to-German translation", "paper_idx": 0,
             "queries": ["What BLEU score did Transformer achieve?", "Transformer WMT 2014 performance",
                         "Translation benchmark results for Transformer"]},
            # Paper 2: BERT
            {"fact": "BERT is designed to pre-train deep bidirectional representations by jointly conditioning on both left and right context", "paper_idx": 1,
             "queries": ["How does BERT pre-training work?", "What makes BERT bidirectional?",
                         "BERT's key innovation in pre-training"]},
            {"fact": "BERT uses masked language modeling and next sentence prediction as pre-training objectives", "paper_idx": 1,
             "queries": ["What are BERT's pre-training tasks?", "BERT training objectives",
                         "MLM and NSP in BERT"]},
            {"fact": "BERT achieved state-of-the-art results on 11 NLP tasks including GLUE, SQuAD, and SWAG", "paper_idx": 1,
             "queries": ["What benchmarks did BERT improve?", "BERT performance on NLP tasks",
                         "Which tasks did BERT set new records on?"]},
            # Paper 3: ViT
            {"fact": "Vision Transformer applies a pure Transformer directly to sequences of image patches", "paper_idx": 2,
             "queries": ["How does ViT process images?", "Vision Transformer image patches",
                         "How does ViT convert images to tokens?"]},
            {"fact": "ViT attains excellent results when pre-trained on large datasets and transferred to mid-sized or small image recognition benchmarks", "paper_idx": 2,
             "queries": ["How well does ViT perform?", "ViT transfer learning results",
                         "Does ViT need large datasets?"]},
            {"fact": "The standard Transformer receives as input a 1D sequence of token embeddings, while ViT handles 2D images", "paper_idx": 2,
             "queries": ["How does ViT differ from standard Transformer?", "ViT input format",
                         "Difference between text Transformer and image Transformer"]},
        ],
    },
}