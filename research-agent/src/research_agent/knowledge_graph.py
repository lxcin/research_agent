"""Knowledge graph: global graph (auto) + per-paper graph (on-demand)."""
import json
import networkx as nx
from dataclasses import dataclass, field
from research_agent.llm import LLMProvider
from research_agent.store import _get_db, init_db


@dataclass
class Claim:
    id: str | None = None
    paper_id: str = ""
    text: str = ""
    claim_type: str = "claim"
    confidence: float = 0.0
    embedding_id: str = ""


@dataclass
class Relation:
    id: str | None = None
    source_claim_id: str = ""
    target_claim_id: str = ""
    relation_type: str = ""
    source_paper_id: str = ""
    target_paper_id: str = ""


class KnowledgeGraph:
    def __init__(self):
        self.graph = nx.DiGraph()

    def add_claim(self, claim: Claim):
        self.graph.add_node(claim.id, claim=claim, paper_id=claim.paper_id)

    def add_relation(self, rel: Relation):
        self.graph.add_edge(rel.source_claim_id, rel.target_claim_id,
                           relation_type=rel.relation_type)

    def get_claims_for_paper(self, paper_id: str) -> list[Claim]:
        nodes = [n for n, d in self.graph.nodes(data=True)
                 if d.get("paper_id") == paper_id]
        return [self.graph.nodes[n]["claim"] for n in nodes]

    def get_related_claims(self, claim_id: str, depth: int = 1) -> list[Claim]:
        related = set()
        queue = [(claim_id, 0)]
        visited = {claim_id}
        while queue:
            cid, d = queue.pop(0)
            if d >= depth:
                continue
            for neighbor in self.graph.neighbors(cid):
                if neighbor not in visited:
                    visited.add(neighbor)
                    related.add(neighbor)
                    queue.append((neighbor, d + 1))
        return [self.graph.nodes[n]["claim"] for n in related]

    def build_argument_tree(self, paper_id: str) -> dict:
        claims = self.get_claims_for_paper(paper_id)
        if not claims:
            return {"paper_id": paper_id, "claims": []}

        root = None
        for c in claims:
            in_edges = [e for e in self.graph.in_edges(c.id)
                       if self.graph.nodes[e[0]].get("paper_id") == paper_id]
            if not in_edges:
                root = c
                break
        if not root:
            root = claims[0]

        return _build_tree(root, self.graph, paper_id)


def _build_tree(claim: Claim, graph: nx.DiGraph, paper_id: str) -> dict:
    children = []
    for _, target in graph.out_edges(claim.id):
        if graph.nodes[target].get("paper_id") == paper_id:
            child_claim = graph.nodes[target]["claim"]
            children.append(_build_tree(child_claim, graph, paper_id))
    return {
        "claim_id": claim.id,
        "text": claim.text,
        "type": claim.claim_type,
        "children": children,
    }


def init_kg_tables():
    init_db()
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            text TEXT NOT NULL,
            claim_type TEXT NOT NULL DEFAULT 'claim',
            confidence REAL DEFAULT 0.0,
            embedding_id TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS kg_relations (
            id TEXT PRIMARY KEY,
            source_claim_id TEXT NOT NULL,
            target_claim_id TEXT NOT NULL,
            relation_type TEXT NOT NULL DEFAULT 'cites',
            source_paper_id TEXT DEFAULT '',
            target_paper_id TEXT DEFAULT ''
        )
    """)
    db.commit()


def save_claim(claim: Claim) -> str:
    import uuid
    init_kg_tables()
    db = _get_db()
    if not claim.id:
        claim.id = str(uuid.uuid4())
    db.execute(
        "INSERT OR REPLACE INTO claims (id, paper_id, text, claim_type, confidence, embedding_id) VALUES (?, ?, ?, ?, ?, ?)",
        (claim.id, claim.paper_id, claim.text, claim.claim_type, claim.confidence, claim.embedding_id)
    )
    db.commit()
    return claim.id


def save_relation(rel: Relation) -> str:
    import uuid
    init_kg_tables()
    db = _get_db()
    if not rel.id:
        rel.id = str(uuid.uuid4())
    db.execute(
        "INSERT OR REPLACE INTO kg_relations (id, source_claim_id, target_claim_id, relation_type, source_paper_id, target_paper_id) VALUES (?, ?, ?, ?, ?, ?)",
        (rel.id, rel.source_claim_id, rel.target_claim_id, rel.relation_type, rel.source_paper_id, rel.target_paper_id)
    )
    db.commit()
    return rel.id


def load_graph() -> KnowledgeGraph:
    init_kg_tables()
    db = _get_db()
    kg = KnowledgeGraph()

    rows = db.execute("SELECT id, paper_id, text, claim_type, confidence, embedding_id FROM claims").fetchall()
    for row in rows:
        claim = Claim(id=row[0], paper_id=row[1], text=row[2], claim_type=row[3], confidence=row[4], embedding_id=row[5])
        kg.add_claim(claim)

    rows = db.execute("SELECT id, source_claim_id, target_claim_id, relation_type, source_paper_id, target_paper_id FROM kg_relations").fetchall()
    for row in rows:
        rel = Relation(id=row[0], source_claim_id=row[1], target_claim_id=row[2], relation_type=row[3], source_paper_id=row[4], target_paper_id=row[5])
        kg.add_relation(rel)

    return kg


def extract_claims(text: str, paper_id: str, llm: LLMProvider) -> list[Claim]:
    prompt = f"""Extract key claims from the following paper text. For each claim, output:
- text: the claim statement
- type: one of [claim, evidence, method, result]
- confidence: 0.0-1.0 based on how clearly the text supports this claim

Output as JSON array:
[{{"text": "...", "type": "claim", "confidence": 0.9}}]

Paper text:
{text[:4000]}"""

    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=1000)
        data = json.loads(raw.strip())
    except Exception:
        return []

    claims = []
    for item in data:
        claim_text = item.get("text", "").strip()
        if not claim_text:
            continue
        if not _verify_claim_in_text(claim_text, text):
            continue
        claims.append(Claim(
            paper_id=paper_id,
            text=claim_text,
            claim_type=item.get("type", "claim"),
            confidence=item.get("confidence", 0.5),
        ))
    return claims


def _verify_claim_in_text(claim: str, text: str) -> bool:
    claim_words = set(claim.lower().split())
    text_words = set(text.lower().split())
    if not claim_words:
        return False
    overlap = len(claim_words & text_words) / len(claim_words)
    return overlap > 0.3


def detect_relations(claims: list[Claim], existing_claims: list[Claim], llm: LLMProvider) -> list[Relation]:
    if not existing_claims:
        return []

    new_text = "\n".join([f"[{c.id}] {c.text}" for c in claims])
    existing_text = "\n".join([f"[{c.id}] {c.text}" for c in existing_claims[:20]])

    prompt = f"""Compare new claims with existing claims. For each pair that has a relationship, output:
- source_id: the existing claim ID
- target_id: the new claim ID
- relation: one of [supports, contradicts, extends, unrelated]

Output JSON array:
[{{"source_id": "...", "target_id": "...", "relation": "supports"}}]

New claims:
{new_text}

Existing claims:
{existing_text}

Only output pairs that have a clear relationship. Skip unrelated pairs."""

    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=500)
        data = json.loads(raw.strip())
    except Exception:
        return []

    relations = []
    for item in data:
        rel_type = item.get("relation", "")
        if rel_type == "unrelated":
            continue
        relations.append(Relation(
            source_claim_id=item["source_id"],
            target_claim_id=item["target_id"],
            relation_type=rel_type,
        ))
    return relations


def build_global_graph_on_ingest(paper_id: str, text: str, llm: LLMProvider) -> KnowledgeGraph:
    new_claims = extract_claims(text, paper_id, llm)
    for claim in new_claims:
        save_claim(claim)

    kg = load_graph()
    existing_claims = [kg.graph.nodes[n]["claim"] for n in kg.graph.nodes]

    relations = detect_relations(new_claims, existing_claims, llm)
    for rel in relations:
        save_relation(rel)

    return load_graph()


def build_paper_argument_tree(paper_id: str, text: str, llm: LLMProvider) -> dict:
    claims = extract_claims(text, paper_id, llm)
    for claim in claims:
        save_claim(claim)

    prompt = f"""Build an argument tree for this paper. The root is the main thesis.
Child nodes are supporting claims, evidence, methods, and results.

Output JSON:
{{
  "thesis": "main thesis statement",
  "children": [
    {{"text": "supporting claim 1", "type": "claim", "children": [
      {{"text": "evidence for claim 1", "type": "evidence", "children": []}}
    ]}}
  ]
}}

Paper text:
{text[:4000]}"""

    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=2000)
        tree = json.loads(raw.strip())
    except Exception:
        tree = {"thesis": "Unable to parse", "children": []}

    return {"paper_id": paper_id, "tree": tree}


def retrieve_from_graph(chunk_paper_id: str, query: str, kg: KnowledgeGraph) -> list[dict]:
    claims = kg.get_claims_for_paper(chunk_paper_id)
    results = []
    for claim in claims:
        related = kg.get_related_claims(claim.id, depth=1)
        for r in related:
            if r.paper_id != chunk_paper_id:
                results.append({
                    "text": r.text,
                    "type": r.claim_type,
                    "paper_id": r.paper_id,
                    "relation": "related via graph",
                })
    return results[:5]