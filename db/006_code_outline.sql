-- Code descriptions and explicit interleaving of articles and structural levels.
ALTER TABLE codes ADD COLUMN description TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS code_outline(
 code_id TEXT NOT NULL REFERENCES codes(id),
 parent_key TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('node','article')),
 item_id TEXT NOT NULL,
 position INTEGER NOT NULL,
 PRIMARY KEY(kind,item_id)
);
CREATE INDEX IF NOT EXISTS ix_code_outline_order
 ON code_outline(code_id,parent_key,position,item_id);

-- Put existing root-level introductory articles before the first division
-- on upgrade; inside other levels retain sections-first legacy ordering.
-- Every resulting position remains editable by the superadministrator.
INSERT INTO code_outline(code_id,parent_key,kind,item_id,position)
SELECT code_id,parent_key,kind,item_id,
       ROW_NUMBER() OVER(PARTITION BY code_id,parent_key
                         ORDER BY grouping, ordering, item_id) * 1000
FROM (
 SELECT n.code_id,COALESCE(n.parent_id,'ROOT') AS parent_key,'node' AS kind,
        n.id AS item_id,CASE WHEN n.parent_id IS NULL THEN 1 ELSE 0 END AS grouping,
        printf('%06d',t.level_order)||':'||t.id||':'||
        CASE WHEN sm.mode='manual'
             THEN '0'||printf('%012d',COALESCE(n.manual_order,999999))
             ELSE '1'||n.sort_key END AS ordering
 FROM structure_nodes n
 JOIN hierarchy_types t ON t.id=n.type_id
 LEFT JOIN sort_modes sm
   ON sm.parent_key=n.code_id||':'||n.parent_key||':'||n.type_id
 WHERE n.status='active'
 UNION ALL
 SELECT a.code_id,COALESCE(a.parent_id,'ROOT'),'article',
        a.id,CASE WHEN a.parent_id IS NULL THEN 0 ELSE 1 END,a.sort_key
 FROM articles a WHERE a.status='active'
);

-- Keep the outline consistent for all writers, including importer and
-- archives, rather than relying on only one HTTP endpoint to maintain it.
CREATE TRIGGER outline_node_insert AFTER INSERT ON structure_nodes
 WHEN NEW.status='active'
BEGIN
 INSERT INTO code_outline(code_id,parent_key,kind,item_id,position)
 SELECT NEW.code_id,COALESCE(NEW.parent_id,'ROOT'),'node',NEW.id,
        COALESCE((SELECT MAX(position) FROM code_outline
                  WHERE code_id=NEW.code_id
                    AND parent_key=COALESCE(NEW.parent_id,'ROOT')),0)+1000;
END;
CREATE TRIGGER outline_node_change AFTER UPDATE OF parent_id,status ON structure_nodes
 WHEN OLD.parent_id IS NOT NEW.parent_id OR OLD.status<>NEW.status
BEGIN
 DELETE FROM code_outline WHERE kind='node' AND item_id=NEW.id;
 INSERT INTO code_outline(code_id,parent_key,kind,item_id,position)
 SELECT NEW.code_id,COALESCE(NEW.parent_id,'ROOT'),'node',NEW.id,
        COALESCE((SELECT MAX(position) FROM code_outline
                  WHERE code_id=NEW.code_id
                    AND parent_key=COALESCE(NEW.parent_id,'ROOT')),0)+1000
 WHERE NEW.status='active';
END;
CREATE TRIGGER outline_node_delete AFTER DELETE ON structure_nodes
BEGIN
 DELETE FROM code_outline WHERE kind='node' AND item_id=OLD.id;
END;
CREATE TRIGGER outline_article_insert AFTER INSERT ON articles
 WHEN NEW.status='active'
BEGIN
 INSERT INTO code_outline(code_id,parent_key,kind,item_id,position)
 SELECT NEW.code_id,COALESCE(NEW.parent_id,'ROOT'),'article',NEW.id,
        COALESCE((SELECT MAX(position) FROM code_outline
                  WHERE code_id=NEW.code_id
                    AND parent_key=COALESCE(NEW.parent_id,'ROOT')),0)+1000;
END;
CREATE TRIGGER outline_article_change AFTER UPDATE OF parent_id,status ON articles
 WHEN OLD.parent_id IS NOT NEW.parent_id OR OLD.status<>NEW.status
BEGIN
 DELETE FROM code_outline WHERE kind='article' AND item_id=NEW.id;
 INSERT INTO code_outline(code_id,parent_key,kind,item_id,position)
 SELECT NEW.code_id,COALESCE(NEW.parent_id,'ROOT'),'article',NEW.id,
        COALESCE((SELECT MAX(position) FROM code_outline
                  WHERE code_id=NEW.code_id
                    AND parent_key=COALESCE(NEW.parent_id,'ROOT')),0)+1000
 WHERE NEW.status='active';
END;
CREATE TRIGGER outline_article_delete AFTER DELETE ON articles
BEGIN
 DELETE FROM code_outline WHERE kind='article' AND item_id=OLD.id;
END;
