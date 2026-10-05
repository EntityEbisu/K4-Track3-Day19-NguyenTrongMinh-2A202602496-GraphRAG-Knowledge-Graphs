"""Run the benchmark with the SUGGESTED (hint) ontology, to produce before/after evidence.

The bonus in SUBMISSION.md requires shipping the hint ontology's own benchmark output as
`ket_qua_benchmark_kg.hint.txt`, so this monkeypatches the two ontology-dependent functions
(build_graph, Neo4jGraph.context) back to the hint design while leaving everything else —
chunking, embeddings, KG-1, KG-4, the judge — untouched.

    .venv/Scripts/python.exe -B scripts/run_hint_baseline.py

Differences from my custom ontology (all reverted here to the hint behaviour):
  * Case keyed by the LLM-invented `name`      -> allows duplicate/forked Case nodes
  * no Threshold nodes, no SETS_THRESHOLD/FOR_SUBSTANCE
  * KG-3 keeps clause 1 + clauses MENTIONing a substance the case INVOLVES
    (no Threshold-based clause selection)
Substance alias merging stays ON so this measures the Case key + Threshold differences only.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from src import graph as G
from src.models import Document


# ------------------------------------------------------------------ hint: build_graph (KG-2)
def hint_build_graph(graph, law_docs, news_docs, llm_fn) -> None:
    """Hint ontology: Case MERGEd on the LLM-supplied name; no Threshold nodes."""
    for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                       ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
        graph.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    articles = [G.parse_law_article(doc) for doc in law_docs]
    for article in articles:
        graph.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text,
                  cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances
                | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            """,
            **article,
        )
    crimes = sorted({a["crime"] for a in articles if a["crime"]})
    for doc in news_docs:
        for case in G.extract_news_cases(doc, lambda p: llm_fn(p, json_mode=True), crimes):
            graph.run(
                """
                MERGE (k:Case {name: $name})
                  SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
                FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                    MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
                FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
                FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                    SET r.amount = s.amount)
                FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                    SET person.aliases = coalesce(p.aliases, [])
                    MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge,
                        r.sentence = p.sentence)
                """,
                name=case.get("name") or doc.metadata.get("title", doc.id),
                summary=case.get("summary", ""), date=case.get("date", ""),
                location=case.get("location", ""), charges=case.get("charges", []),
                people=[p for p in case.get("people", []) if p.get("name")],
                substances=[s for s in case.get("substances", []) if s.get("name")],
                doc_id=doc.id, title=doc.metadata.get("title", ""),
            )


# ------------------------------------------------------------------ hint: context (KG-3)
def hint_context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
    """Hint ontology: clause 1 + clauses MENTIONing a substance the case INVOLVES."""
    seed_ids, facts = self.seed_facts(question, doc_ids, skip_labels=("Threshold",))
    seen: set[str] = set()
    ordered: list[str] = []
    for fact in facts:
        if fact not in seen:
            seen.add(fact)
            ordered.append(fact)

    def add(text: str) -> None:
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)

    cases = self.run(
        """
        MATCH (k:Case)
        WHERE elementId(k) IN $ids OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
        RETURN elementId(k) AS id, k.name AS name, k.summary AS summary
        """,
        ids=seed_ids,
    )
    for case in cases:
        add(f"Vụ việc '{case['name']}': {case['summary']}")
        rows = self.run(
            """
            MATCH (k:Case)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE elementId(k) IN $case_ids
            WITH cl, a, collect(DISTINCT c.name) AS crimes,
                 [(k)-[:INVOLVES]->(s:Substance) | s.name] AS case_substances
            WITH cl, a, crimes, case_substances,
                 [(cl)-[:MENTIONS]->(s:Substance) | s.name] AS clause_substances
            WITH cl, a, crimes,
                 cl.number = 1 AS is_baseline,
                 any(s IN clause_substances WHERE s IN case_substances) AS shares_substance
            WITH cl, a, crimes, CASE WHEN is_baseline OR shares_substance THEN true ELSE false END AS keep
            WHERE keep
            RETURN a.id AS article_id, a.title AS title, cl.number AS number, cl.penalty AS penalty,
                   crimes AS crimes
            ORDER BY a.id, cl.number
            """,
            case_ids=[case["id"] for case in cases],
        )
        for row in rows:
            penalty = f" ({row['penalty']})" if row["penalty"] else ""
            add(f"[{row['article_id']} - {row['title']}] khoản {row['number']}"
                f"{penalty}: tội {', '.join(row['crimes'])}")

    for number in re.findall(r"[Đđ]iều\s+(\d+)", question):
        for row in self.run(
            """
            MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE a.id CONTAINS $number
            RETURN a.id AS article_id, a.title AS title, cl.number AS number, cl.penalty AS penalty
            ORDER BY cl.number LIMIT 12
            """,
            number=number,
        ):
            penalty = f" ({row['penalty']})" if row["penalty"] else ""
            add(f"[{row['article_id']} - {row['title']}] khoản {row['number']}{penalty}")

    return ordered[:max_facts]


def main() -> int:
    G.build_graph = hint_build_graph
    G.Neo4jGraph.context = hint_context

    import bench_kg
    bench_kg.graph_mod = G
    sys.argv = ["bench_kg.py", "--judge", "--out", "ket_qua_benchmark_kg.hint.txt"]
    print("[hint baseline] chạy lại benchmark với ontology GỢI Ý -> ket_qua_benchmark_kg.hint.txt")
    return bench_kg.main()


if __name__ == "__main__":
    raise SystemExit(main())