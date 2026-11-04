"""Offline database maintenance for the legal library (stdlib only).

Always stop the ASGI server before restore/migrate. Backups use SQLite's
consistent online backup API; copies are verified via PRAGMA integrity_check.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def verify(path):
    source=Path(path).resolve()
    if not source.is_file():
        raise RuntimeError("Файл базы данных не найден: "+str(source))
    c=sqlite3.connect(str(source),timeout=5)
    try:
        result=c.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0]!="ok":
            raise RuntimeError("Проверка целостности базы данных не пройдена: "+str(result))
        version=c.execute("PRAGMA user_version").fetchone()[0]
        schema=c.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        return {"file":str(source),"schema_versions":[int(x[0]) for x in schema],
                "sqlite_user_version":version}
    finally:
        c.close()


def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for part in iter(lambda:f.read(1024*1024),b""):
            h.update(part)
    return h.hexdigest()


def consistent_copy(src,dst):
    origin=sqlite3.connect(str(src),timeout=30)
    target=sqlite3.connect(str(dst),timeout=30)
    try:
        origin.backup(target,pages=128,sleep=.2)
    finally:
        origin.close()
        target.close()
    verify(dst)


def backup(database,destination=None):
    db=Path(database).resolve()
    if not db.is_file():
        raise RuntimeError("Исходная БД не найдена: "+str(db))
    now=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target=Path(destination).resolve() if destination else (
        db.parent / "backups" / ("library_"+now+".sqlite"))
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        raise RuntimeError("Файл резервной копии уже существует: "+str(target))
    consistent_copy(db,target)
    result=verify(target)
    result["sha256"]=digest(target)
    result["created_utc"]=datetime.now(timezone.utc).isoformat()
    target.with_suffix(target.suffix+".json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return target


def restore(database,source,confirm=False):
    if not confirm:
        raise RuntimeError("Для восстановления добавьте параметр --confirm.")
    target=Path(database).resolve()
    source=Path(source).resolve()
    if source==target:
        raise RuntimeError("Нельзя восстановить БД из самой себя.")
    verify(source)
    if target.exists():
        safety=target.parent / "backups" / (
            "before_restore_"+datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")+".sqlite")
        backup(target,safety)
    target.parent.mkdir(parents=True,exist_ok=True)
    consistent_copy(source,target)
    print("База данных восстановлена и проверена: "+str(target))


def main(argv=None):
    parser=argparse.ArgumentParser(description="Резервные копии и миграции Правовой библиотеки.")
    parser.add_argument("--database",default=os.getenv("DATABASE_PATH","./sas_portal.db"))
    commands=parser.add_subparsers(dest="command",required=True)
    b=commands.add_parser("backup");b.add_argument("output",nargs="?")
    v=commands.add_parser("verify");v.add_argument("file",nargs="?")
    r=commands.add_parser("restore");r.add_argument("source");r.add_argument("--confirm",action="store_true")
    commands.add_parser("migrate")
    args=parser.parse_args(argv)
    try:
        if args.command=="backup":
            backup(args.database,args.output)
        elif args.command=="verify":
            print(json.dumps(verify(args.file or args.database),ensure_ascii=False,indent=2))
        elif args.command=="restore":
            restore(args.database,args.source,args.confirm)
        elif args.command=="migrate":
            if Path(args.database).is_file():
                backup(args.database)
            sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
            import app.core as core
            core.DB_PATH=str(Path(args.database).resolve())
            core.init_database()
            print("Миграции применены: "+str(args.database))
            print(json.dumps(verify(args.database),ensure_ascii=False,indent=2))
    except (RuntimeError,sqlite3.Error,OSError) as exc:
        parser.exit(1,"Ошибка: "+str(exc)+"\n")


if __name__=="__main__":
    main()
