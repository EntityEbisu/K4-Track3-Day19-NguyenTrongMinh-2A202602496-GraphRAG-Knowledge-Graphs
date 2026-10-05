"""Collect Cypher evidence for the error analysis in report/REPORT_KG.md (section 3).

Read-only. Prints the exact query and its result for each finding, so the report can quote
verifiable output instead of assertions.

    .venv/Scripts/python.exe -B scripts/error_evidence.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from src.graph import Neo4jGraph

CHECK_NEWS = "news-100260918080821054"

EVIDENCE: list[tuple[str, str, dict]] = [
    (
        "E1 — Cases with no bridge to the law KB",
        "MATCH (k:Case) WHERE NOT (k)-[:CHARGED_WITH]->() "
        "RETURN k.key AS key, k.name AS name, k.doc_id AS doc_id ORDER BY k.doc_id",
        {},
    ),
    (
        "E1b — how many cases DO bridge",
        "MATCH (k:Case)-[:CHARGED_WITH]->(c:Crime) RETURN count(*) AS bridged_cases",
        {},
    ),
    (
        "E2 — MDMA mass ladder actually stored (Q5)",
        "MATCH (cl:Clause)-[:SETS_THRESHOLD]->(t:Threshold) "
        "WHERE t.substance = 'MDMA' AND cl.id STARTS WITH 'Điều 250' "
        "RETURN cl.number AS khoan, t.min_g AS min_g, t.max_g AS max_g, "
        "       cl.penalty AS penalty ORDER BY t.min_g",
        {},
    ),
    (
        "E2b — thresholds the law text mentions but the parser could not bind to a substance",
        "MATCH (cl:Clause)-[:SETS_THRESHOLD]->(t:Threshold) "
        "RETURN count(*) AS total_thresholds",
        {},
    ),
    (
        "E3 — Substance nodes (alias merging check)",
        "MATCH (s:Substance) RETURN s.name AS name, "
        "       count { (s)<-[:MENTIONS]-() } AS mentioned_by_law, "
        "       count { (s)<-[:INVOLVES]-() }  AS used_by_news ORDER BY name",
        {},
    ),
    (
        "E3b — Person nodes keyed by LLM-invented name",
        "MATCH (p:Person) RETURN p.name AS name, p.aliases AS aliases, "
        "       count { (p)-[:INVOLVED_IN]->() } AS cases ORDER BY name",
        {},
    ),
    (
        "E5 — cross-KB path for the Q3 case, straight from Cypher",
        "MATCH (p:Person {name:'Lê Minh Thành'})-[:INVOLVED_IN]->(k:Case)"
        "-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article) "
        "RETURN p.name AS person, k.name AS case, c.name AS crime, a.id AS article, "
        "       k.summary AS summary",
        {},
    ),
    (
        "E5b — what the graph says about Q4 (Hoàng Nato)",
        "MATCH (p:Person)-[:INVOLVED_IN]->(k:Case)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article) "
        "WHERE any(al IN coalesce(p.aliases, []) WHERE toLower(al) CONTAINS 'nato') "
        "RETURN p.name AS person, p.aliases AS aliases, c.name AS crime, a.id AS article",
        {},
    ),
    (
        "E6 — INVOLVED_IN edges with an empty charge",
        "MATCH (p:Person)-[r:INVOLVED_IN]->(k:Case) WHERE coalesce(r.charge, '') = '' "
        "RETURN p.name AS person, r.role AS role, r.sentence AS sentence, k.name AS case",
        {},
    ),
    (
        "E6b — INVOLVES edges with an empty amount",
        "MATCH (k:Case)-[r:INVOLVES]->(s:Substance) WHERE coalesce(r.amount, '') = '' "
        "RETURN k.name AS case, s.name AS substance",
        {},
    ),
]


def main() -> int:
    graph = Neo4jGraph("bolt://localhost:7687", "neo4j", "password123")
    stats = graph.stats()
    print(f"# Graph hiện tại: {stats['nodes']} node / {stats['relationships']} cạnh\n")
    for title, cypher, params in EVIDENCE:
        print("=" * 78)
        print(title)
        print("-" * 78)
        print(cypher)
        rows = graph.run(cypher, **params)
        if not rows:
            print("  (không có dòng nào)")
        for row in rows:
            print("   ", row)
        print()
    graph.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())