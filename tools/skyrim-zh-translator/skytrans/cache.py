"""翻译缓存（SQLite）：断点续翻、避免重复计费、支持人工校对回流。"""

from __future__ import annotations

import csv
import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


class Cache:
    def __init__(self, path: Path, namespace: str = "default") -> None:
        self.path = path
        self.namespace = namespace
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                namespace TEXT NOT NULL,
                updated REAL NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ns ON translations(namespace)"
        )
        self.conn.commit()

    # ---------- key ----------
    def key(self, text: str) -> str:
        raw = "%s\x00%s" % (self.namespace, text)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    # ---------- 读写 ----------
    def get_many(self, namespaces: Iterable[str], texts: Iterable[str]) -> Dict[str, str]:
        texts = list(dict.fromkeys(texts))
        out: Dict[str, str] = {}
        CHUNK = 500
        for i in range(0, len(texts), CHUNK):
            part = texts[i : i + CHUNK]
            keys = {self.key(t): t for t in part}
            marks = ",".join("?" * len(keys))
            for key, target in self.conn.execute(
                "SELECT key, target FROM translations WHERE key IN (%s)" % marks,
                list(keys),
            ):
                out[keys[key]] = target
        return out

    def put_many(self, namespace: str, pairs: Iterable[Tuple[str, str]]) -> None:
        now = time.time()
        rows = [
            (self.key(src), src, dst, namespace, now)
            for src, dst in pairs
            if src and dst is not None
        ]
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO translations(key, source, target, namespace, updated)"
            " VALUES(?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def get_one(self, text: str) -> str | None:
        row = self.conn.execute(
            "SELECT target FROM translations WHERE key = ?", (self.key(text),)
        ).fetchone()
        return row[0] if row else None

    def put_one(self, source: str, target: str) -> None:
        self.put_many(self.namespace, [(source, target)])

    # ---------- 人工校对 ----------
    def export_csv(self, path: Path, only_namespace: bool = True) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        sql = "SELECT source, target FROM translations"
        args: List[str] = []
        if only_namespace:
            sql += " WHERE namespace = ?"
            args.append(self.namespace)
        sql += " ORDER BY updated DESC"
        rows = self.conn.execute(sql, args).fetchall()
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["原文", "译文"])
            writer.writerows(rows)
        return len(rows)

    def import_csv(self, path: Path) -> int:
        """把人工修改过的 CSV 写回缓存，返回更新条数。"""
        updated = 0
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            rows = list(reader)
        if not rows:
            return 0
        start = 1 if rows[0] and rows[0][0].strip() in ("原文", "source", "SOURCE") else 0
        pairs = []
        for row in rows[start:]:
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                pairs.append((row[0], row[1]))
        if pairs:
            self.put_many(self.namespace, pairs)
            updated = len(pairs)
        return updated

    def stats(self) -> Dict[str, int]:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM translations WHERE namespace = ?", (self.namespace,)
        ).fetchone()[0]
        return {"cached": int(total)}

    def close(self) -> None:
        self.conn.close()
