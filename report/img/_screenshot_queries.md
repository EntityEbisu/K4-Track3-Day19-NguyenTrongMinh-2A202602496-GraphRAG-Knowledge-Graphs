# Hướng dẫn chụp 3 ảnh Neo4j Browser cho bài nộp

Điều kiện tiên quyết: graph phải là **ontology của tôi** (435 node / 853 cạnh), không phải bản
ontology gợi ý. Nếu vừa chạy `scripts/run_hint_baseline.py` thì phải chạy lại
`python bench_kg.py --build` trước.

## Chuẩn bị

1. Bật Docker Desktop, đợi biểu tượng chuyển xanh.
2. `docker start neo4j-drug-kg`, đợi khoảng 20 giây.
3. Mở trình duyệt tại **http://localhost:7474**
4. Đăng nhập: protocol `neo4j://`, host `localhost:7687`, user `neo4j`, password `password123`.
5. **Phóng to cửa sổ trình duyệt** (khuyến nghị toàn màn hình, tối thiểu 1600×900). Cửa sổ nhỏ quá
   thì Neo4j Browser **không hiện bảng "Results overview"** ở bên phải — ảnh sẽ không đạt.
6. Nếu có bảng giới thiệu lần đầu (tour), bấm **Dismiss** để nó không che kết quả.

## Quy tắc ảnh (theo SUBMISSION.md / LAB_GUIDE 8.2)

- Chụp **cả cửa sổ trình duyệt**: phải thấy **ô truy vấn** và **Results overview**.
- **Không cắt, không chỉnh sửa** ảnh.
- Gõ `:clear` trước mỗi truy vấn để mỗi ảnh chỉ có **một** khung kết quả.

---

## Ảnh 1 — `report/img/kg_count.png` (Q-A: đếm node theo loại)

Dán vào ô `$` rồi bấm **Run** (hoặc `Ctrl+Enter`):

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC;
```

Kết quả cần thấy (8 label, đúng số này):
`Threshold 221`, `Clause 99`, `Person 38`, `Article 18`, `Substance 18`, `Case 17`,
`Crime 13`, `Location 11`.

---

## Ảnh 2 — `report/img/kg_cross_kb.png` (Q-B: cầu nối 2 KB)

```cypher
MATCH p=(:Person)-[:INVOLVED_IN]->(:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(:Article)
RETURN p LIMIT 25;
```

Truy vấn này trả về **Path** nên Browser tự mở tab **Graph** và hiện **Results overview**:
Nodes 31 (Person 21, Case 6, Crime 2, Article 2), Relationships 33 (INVOLVED_IN 25,
CHARGED_WITH 6, DEFINES 2). Phải thấy đủ node/cạnh trên đường đi.

---

## Ảnh 3 — `report/img/kg_my_case.png` (Q-D: một vụ tự chọn)

Người đã chọn: **Dương Minh Tuấn (biệt danh "Hoàng Nato")** — khác `Lê Minh Thành` như yêu cầu.

```cypher
MATCH p=(:Person)-[:INVOLVED_IN]->(k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(:Article)
WHERE p.name = 'Dương Minh Tuấn'
OPTIONAL MATCH q=(k)-[:INVOLVES|LOCATED_IN]->()
RETURN p, q;
```

Phải thấy đường đi **người → vụ → tội → Điều luật** (Điều 255 BLHS), kèm chất/địa điểm của vụ.

---

## Sau khi chụp

Lưu đúng tên file vào `report/img/`:

- `kg_count.png`
- `kg_cross_kb.png`
- `kg_my_case.png`

Kiểm tra nhanh: mở từng ảnh, xác nhận **thấy ô truy vấn** + **thấy Results overview** + **không bị
tour che**. Rồi chạy lại phần tự kiểm ở mục 5 của `REPORT_KG.md`.
