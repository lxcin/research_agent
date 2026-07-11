from research_agent.knowledge_graph import (
    KnowledgeGraph, Claim, Relation, save_claim, save_relation,
    load_graph, init_kg_tables, extract_claims, _verify_claim_in_text,
    build_paper_argument_tree, retrieve_from_graph,
)
from research_agent.llm import MockLLMProvider
from research_agent.store import init_db


def test_knowledge_graph_add_claim(temp_data_dir):
    kg = KnowledgeGraph()
    claim = Claim(id="c1", paper_id="p1", text="Test claim", claim_type="claim")
    kg.add_claim(claim)
    assert "c1" in kg.graph.nodes
    assert kg.graph.nodes["c1"]["claim"].text == "Test claim"


def test_knowledge_graph_add_relation(temp_data_dir):
    kg = KnowledgeGraph()
    kg.add_claim(Claim(id="c1", paper_id="p1", text="Claim 1"))
    kg.add_claim(Claim(id="c2", paper_id="p2", text="Claim 2"))
    kg.add_relation(Relation(id="r1", source_claim_id="c1", target_claim_id="c2", relation_type="supports"))
    assert kg.graph.has_edge("c1", "c2")


def test_get_claims_for_paper(temp_data_dir):
    kg = KnowledgeGraph()
    kg.add_claim(Claim(id="c1", paper_id="p1", text="C1"))
    kg.add_claim(Claim(id="c2", paper_id="p1", text="C2"))
    kg.add_claim(Claim(id="c3", paper_id="p2", text="C3"))
    claims = kg.get_claims_for_paper("p1")
    assert len(claims) == 2


def test_get_related_claims(temp_data_dir):
    kg = KnowledgeGraph()
    kg.add_claim(Claim(id="c1", paper_id="p1", text="C1"))
    kg.add_claim(Claim(id="c2", paper_id="p2", text="C2"))
    kg.add_claim(Claim(id="c3", paper_id="p2", text="C3"))
    kg.add_relation(Relation(source_claim_id="c1", target_claim_id="c2", relation_type="supports"))
    kg.add_relation(Relation(source_claim_id="c2", target_claim_id="c3", relation_type="extends"))
    related = kg.get_related_claims("c1", depth=1)
    assert len(related) == 1


def test_verify_claim_in_text():
    assert _verify_claim_in_text("attention mechanism is fast", "the attention mechanism is very fast and efficient")
    assert not _verify_claim_in_text("completely made up claim", "this text has nothing to do with it")


def test_save_and_load_claims(temp_data_dir):
    init_db()
    init_kg_tables()
    claim = Claim(paper_id="p1", text="Test claim", claim_type="claim")
    cid = save_claim(claim)
    assert cid is not None
    kg = load_graph()
    assert len(kg.graph.nodes) >= 1


def test_save_and_load_relations(temp_data_dir):
    init_db()
    init_kg_tables()
    c1 = Claim(paper_id="p1", text="C1")
    c2 = Claim(paper_id="p2", text="C2")
    save_claim(c1)
    save_claim(c2)
    rel = Relation(source_claim_id=c1.id, target_claim_id=c2.id, relation_type="supports")
    save_relation(rel)
    kg = load_graph()
    assert kg.graph.has_edge(c1.id, c2.id)


def test_extract_claims_with_mock_llm(temp_data_dir):
    llm = MockLLMProvider(['[{"text": "attention is fast", "type": "claim", "confidence": 0.9}]'])
    claims = extract_claims("attention is fast and efficient", "p1", llm)
    assert len(claims) >= 1
    assert claims[0].text == "attention is fast"


def test_build_argument_tree(temp_data_dir):
    kg = KnowledgeGraph()
    c1 = Claim(id="c1", paper_id="p1", text="Main thesis", claim_type="claim")
    c2 = Claim(id="c2", paper_id="p1", text="Supporting evidence", claim_type="evidence")
    c3 = Claim(id="c3", paper_id="p2", text="Other paper", claim_type="claim")
    kg.add_claim(c1)
    kg.add_claim(c2)
    kg.add_claim(c3)
    kg.add_relation(Relation(source_claim_id="c1", target_claim_id="c2", relation_type="supports"))
    tree = kg.build_argument_tree("p1")
    assert tree["claim_id"] == "c1"
    assert len(tree["children"]) > 0


def test_retrieve_from_graph(temp_data_dir):
    kg = KnowledgeGraph()
    c1 = Claim(id="c1", paper_id="p1", text="C1")
    c2 = Claim(id="c2", paper_id="p2", text="C2")
    kg.add_claim(c1)
    kg.add_claim(c2)
    kg.add_relation(Relation(source_claim_id="c1", target_claim_id="c2", relation_type="supports"))
    results = retrieve_from_graph("p1", "test", kg)
    assert len(results) >= 1
    assert results[0]["paper_id"] == "p2"