"""Regression tests for calendar-based audit retention and Part I source import."""
from datetime import datetime, timedelta, timezone

import app.core as core
from scripts.import_penal_part_i import import_part_i


def test_audit_retention_removes_only_events_older_than_six_months(client, headers):
    old_time = (datetime.now(timezone.utc) - timedelta(days=220)).isoformat()
    recent_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with core.transaction() as c:
        for key, when in (("old-audit", old_time), ("recent-audit", recent_time)):
            c.execute("""INSERT INTO audit_events
                      (id,user_id,action,object_type,object_id,before_json,after_json,created_at)
                      VALUES(?,?,?,?,?,?,?,?)""",
                      (key, "tester", "sample", "article", "demo-article-1", "{}", "{}", when))
        revisions = c.execute("SELECT COUNT(*) FROM article_revisions").fetchone()[0]
    assert core.purge_old_audit_events() == 1
    with core.read_db() as c:
        ids = {r[0] for r in c.execute("SELECT id FROM audit_events")}
        assert "old-audit" not in ids
        assert "recent-audit" in ids
        assert c.execute("SELECT COUNT(*) FROM article_revisions").fetchone()[0] == revisions
    assert core.purge_old_audit_events() == 0


def test_part_i_import_is_complete_and_idempotent(client, headers):
    preview = import_part_i()
    assert preview["sections"] == 18 and preview["articles"] == 104
    first = import_part_i(apply=True)
    assert first["articles_created"] == 104
    assert first["sections_created"] == 19  # Including Part I itself.
    assert first["code_id"] == "PEN"
    second = import_part_i(apply=True)
    assert second["articles_created"] == 0
    assert second["sections_created"] == 0
    with core.read_db() as c:
        article = core.one(c, "SELECT * FROM articles WHERE id='pen-i-article-187'")
        assert article["number"] == "187"
        assert "Убийство" in article["title"]
        assert article["is_demo"] == 1
        assert article["criminal_class"] is None
        assert "pen-i-unclassified-accusation" in core.loads(article["categories_json"])
        assert c.execute("""SELECT COUNT(*) FROM articles
                 WHERE id LIKE 'pen-i-article-%'""").fetchone()[0] == 104
        assert c.execute("""SELECT COUNT(*) FROM article_revisions
                 WHERE article_id LIKE 'pen-i-article-%'""").fetchone()[0] == 104


def test_import_conflict_rolls_back_everything(client, admin):
    admin("25", title="Existing article number")
    try:
        import_part_i(apply=True)
    except ValueError as error:
        assert "conflicts" in str(error)
    else:
        raise AssertionError("Expected number conflict")
    with core.read_db() as c:
        assert c.execute("""SELECT COUNT(*) FROM articles
                WHERE id LIKE 'pen-i-article-%'""").fetchone()[0] == 0
        assert c.execute("""SELECT COUNT(*) FROM structure_nodes
                WHERE id LIKE 'pen-i-%'""").fetchone()[0] == 0
