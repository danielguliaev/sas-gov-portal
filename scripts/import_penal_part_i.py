"""Idempotent import of the supplied, unapproved Penal Code Part I into the library.

Never invent criminal classes or sanctions. Existing article numbers are protected.
Imported texts remain marked as source material, not an official enactment.
"""
import argparse
import hashlib
import json
from pathlib import Path

from app import core

SOURCE = Path(__file__).resolve().parent.parent / "data" / "pen_part_i.json"
PART_TYPE_ID = "pen-i-type-part"
SECTION_TYPE_ID = "pen-i-type-section"
PART_NODE_ID = "pen-i-part-1"
PENDING_CATEGORY_ID = "pen-i-unclassified-accusation"
AUX_CATEGORY_ID = "pen-i-auxiliary-norm"


def import_part_i(database=None, *, apply=False):
    if database is not None:
        core.DB_PATH = str(Path(database).resolve())
    data_bytes = SOURCE.read_bytes()
    data = json.loads(data_bytes)
    if data.get("code") != "PEN" or len(data.get("sections", [])) != 18 or len(data.get("articles", [])) != 104:
        raise ValueError("Unexpected Penal Code Part I source structure")
    fingerprint = hashlib.sha256(data_bytes).hexdigest()
    sections = data["sections"]
    articles = data["articles"]
    section_ids = {item["id"]: "pen-i-" + item["id"] for item in sections}
    if len(section_ids) != len(sections) or len({x["number"] for x in articles}) != len(articles):
        raise ValueError("Duplicate sections or article numbers in import")
    if any(item["section"] not in section_ids for item in articles):
        raise ValueError("Article points to an unknown section")
    if not apply:
        return {"sections": len(sections), "articles": len(articles), "sha256": fingerprint, "applied": False}

    # Schema/seed prerequisites are explicit; the importer does not delete or update
    # existing legal content, and the whole import commits atomically.
    core.init_database()
    core.seed_demo(include_sample=False)
    with core.transaction() as c:
        matches = core.allrows(c, "SELECT id FROM codes WHERE jurisdiction_id='state' AND name='Penal Code' AND status='active'")
        if len(matches) > 1:
            raise ValueError("Ambiguous target Penal Code")
        if matches:
            code_id = matches[0]["id"]
        else:
            code_id = "PEN"
            if core.maybe(c, "SELECT id FROM codes WHERE id=?", (code_id,)):
                raise ValueError("Reserved code ID is already in use")
            c.execute("INSERT INTO codes (id,jurisdiction_id,name,abbreviation,is_demo) VALUES (?,?,?,?,1)",
                      (code_id, "state", "Penal Code", "PEN"))

        for cat_id, name in (
            (PENDING_CATEGORY_ID, "Обвинение — требуется указать уголовно-правовой класс"),
            (AUX_CATEGORY_ID, "Вспомогательная норма"),
        ):
            existing = core.maybe(c, "SELECT name,code_id FROM categories WHERE id=?", (cat_id,))
            if existing and (existing["name"] != name or existing["code_id"] != code_id):
                raise ValueError("Category ID collision: " + cat_id)
            c.execute("INSERT OR IGNORE INTO categories(id,code_id,name) VALUES(?,?,?)",
                      (cat_id, code_id, name))

        for type_id, name, order, parents, fmt in (
            (PART_TYPE_ID, "Часть", 1, ["ROOT"], "roman"),
            (SECTION_TYPE_ID, "Раздел", 2, [PART_TYPE_ID], "mixed"),
        ):
            existing = core.maybe(c, "SELECT code_id,name FROM hierarchy_types WHERE id=?", (type_id,))
            if existing and (existing["code_id"] != code_id or existing["name"] != name):
                raise ValueError("Hierarchy type ID collision: " + type_id)
            c.execute("""INSERT OR IGNORE INTO hierarchy_types
                         (id,code_id,name,level_order,allowed_parents,number_format)
                         VALUES (?,?,?,?,?,?)""",
                      (type_id, code_id, name, order, core.dumps(parents), fmt))

        nodes = [(PART_NODE_ID, PART_TYPE_ID, None, data["part"])]
        nodes += [(section_ids[x["id"]], SECTION_TYPE_ID, PART_NODE_ID, x) for x in sections]
        created_nodes = 0
        for node_id, type_id, parent, item in nodes:
            if core.maybe(c, "SELECT id FROM structure_nodes WHERE id=?", (node_id,)):
                node = core.one(c, "SELECT code_id,number,title,parent_id FROM structure_nodes WHERE id=?", (node_id,))
                if (node["code_id"], node["number"], node["title"], node["parent_id"]) != (
                    code_id, item["number"], item["title"], parent):
                    raise ValueError("Existing imported node has been modified: " + node_id)
                continue
            collision = core.maybe(c, """SELECT id FROM structure_nodes WHERE code_id=?
                      AND type_id=? AND parent_key=? AND number_key=? AND status='active'""",
                      (code_id, type_id, parent or "ROOT", core.normalize_number(item["number"])))
            if collision:
                raise ValueError("Hierarchy number conflict: " + str(item["number"]))
            fmt = "roman" if parent is None else "mixed"
            c.execute("""INSERT INTO structure_nodes
                      (id,code_id,type_id,parent_id,parent_key,number,number_key,sort_key,title)
                      VALUES (?,?,?,?,?,?,?,?,?)""",
                      (node_id, code_id, type_id, parent, parent or "ROOT",
                       item["number"], core.normalize_number(item["number"]),
                       core.number_sort_key(item["number"], fmt), item["title"]))
            created_nodes += 1

        created_articles = 0
        for item in articles:
            num = item["number"]
            article_id = "pen-i-article-" + num.replace(".", "-")
            if core.maybe(c, "SELECT id FROM articles WHERE id=?", (article_id,)):
                row = core.one(c, "SELECT code_id,number FROM articles WHERE id=?", (article_id,))
                if row["code_id"] != code_id or row["number"] != num:
                    raise ValueError("Existing imported article has incompatible identity: " + num)
                continue  # Never overwrite subsequent editorial revisions.
            if core.maybe(c, """SELECT id FROM articles WHERE code_id=? AND number_key=? AND status='active'""",
                          (code_id, core.normalize_number(num))):
                raise ValueError("Existing active article number conflicts with imported article: " + num)
            source_category = item["source_category"]
            categories = {
                "Обвинение": [PENDING_CATEGORY_ID],
                "Вспомогательная норма": [AUX_CATEGORY_ID],
                "Отягчающее обстоятельство": ["aggravating"],
            }.get(source_category)
            if categories is None:
                raise ValueError("Unknown source category: " + source_category)
            now = core.ts()
            c.execute("""INSERT INTO articles
                      (id,code_id,parent_id,number,number_key,sort_key,title,text_rich,
                       is_demo,categories_json,created_at,updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (article_id, code_id, section_ids[item["section"]], num,
                       core.normalize_number(num), core.number_sort_key(num),
                       core.plain(item["title"],300), core.clean(item["text_html"]),
                       1, core.dumps(categories), now, now))
            core.append_revision(c, article_id, "import:pen-part-i",
                                 "Импорт подготовленной части I Penal Code",
                                 provenance={"source_category": source_category,
                                             "import_source": "PEN Part I JSON",
                                             "sha256": fingerprint,
                                             "review_status": "unverified"})
            created_articles += 1
        if created_nodes or created_articles:
            core.audit(c, {"id": "import:pen-part-i", "audit_source": "script"}, "penal.import",
                       "code", code_id, {}, {"new_nodes": created_nodes,
                                             "new_articles": created_articles,
                                             "source_sha256": fingerprint})
        return {"code_id": code_id, "sections_created": created_nodes,
                "articles_created": created_articles, "articles_total": len(articles),
                "applied": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None, help="SQLite file (defaults to DATABASE_PATH)")
    parser.add_argument("--apply", action="store_true",
                        help="Commit import after checking conflicts (default is source preview)")
    args = parser.parse_args()
    print(json.dumps(import_part_i(args.database, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
