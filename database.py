from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class DomainError(ValueError):
    """Business rule violation."""


WITNESS_KINDS = {"version", "fragment", "transcription"}
SPECIAL_TOKENS = {"[缺页]", "[不可辨]", "[残损]", "[插入]", "[删除]"}
GAP_MARKS = ("缺页", "不可辨", "残损")
GAP_STATUSES = {"confirmed", "pending", "explained"}
GAP_STATUS_LABEL = {"confirmed": "据实缺失", "pending": "待补", "explained": "已说明"}
_MARK_RE = re.compile(r"\[(缺页|不可辨|残损)\]")


def validate_transcription(text: str) -> str:
    text = text.strip()
    if not text:
        raise DomainError("文本不能为空")
    unclosed = text.count("[") - text.count("]")
    if unclosed:
        raise DomainError("校勘标记括号不匹配")
    return text


class CollationDB:
    """SQLite-backed textual collation service with optimistic revisions."""

    def __init__(self, path: str = "collation.db") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._schema()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              role TEXT NOT NULL CHECK(role IN ('owner','editor','reviewer'))
            );
            CREATE TABLE IF NOT EXISTS works (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              owner_id INTEGER NOT NULL REFERENCES users(id),
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_access (
              work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id),
              permission TEXT NOT NULL CHECK(permission IN ('view','review')),
              PRIMARY KEY(work_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS witnesses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
              siglum TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('version','fragment','transcription')),
              source_note TEXT NOT NULL DEFAULT '',
              missing_sections TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(work_id,siglum)
            );
            CREATE TABLE IF NOT EXISTS witness_editors (
              witness_id INTEGER NOT NULL REFERENCES witnesses(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id),
              granted_by INTEGER NOT NULL REFERENCES users(id),
              PRIMARY KEY(witness_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS passages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
              label TEXT NOT NULL,
              base_text TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','locked')),
              revision INTEGER NOT NULL DEFAULT 0,
              updated_by INTEGER NOT NULL REFERENCES users(id),
              updated_at TEXT NOT NULL,
              UNIQUE(work_id,label)
            );
            CREATE TABLE IF NOT EXISTS alignments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              passage_id INTEGER NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
              witness_id INTEGER NOT NULL REFERENCES witnesses(id) ON DELETE CASCADE,
              aligned_text TEXT NOT NULL,
              sort_order INTEGER NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              created_by INTEGER NOT NULL REFERENCES users(id),
              created_at TEXT NOT NULL,
              UNIQUE(passage_id,witness_id)
            );
            CREATE TABLE IF NOT EXISTS variants (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              passage_id INTEGER NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
              witness_id INTEGER NOT NULL REFERENCES witnesses(id),
              base_text TEXT NOT NULL,
              proposed_text TEXT NOT NULL,
              reason TEXT NOT NULL,
              layer INTEGER NOT NULL DEFAULT 1,
              created_by INTEGER NOT NULL REFERENCES users(id),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS revisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              passage_id INTEGER NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
              variant_id INTEGER REFERENCES variants(id) ON DELETE CASCADE,
              revision_no INTEGER NOT NULL,
              layer INTEGER NOT NULL,
              snapshot_json TEXT NOT NULL,
              author_id INTEGER NOT NULL REFERENCES users(id),
              created_at TEXT NOT NULL,
              UNIQUE(passage_id, revision_no)
            );
            CREATE TABLE IF NOT EXISTS notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
              body TEXT NOT NULL,
              author_id INTEGER NOT NULL REFERENCES users(id),
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS passage_locks (
              passage_id INTEGER PRIMARY KEY REFERENCES passages(id) ON DELETE CASCADE,
              locked_by INTEGER NOT NULL REFERENCES users(id),
              reason TEXT NOT NULL DEFAULT '',
              locked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gaps (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
              passage_id INTEGER NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
              witness_id INTEGER NOT NULL REFERENCES witnesses(id) ON DELETE CASCADE,
              mark TEXT NOT NULL CHECK(mark IN ('缺页','不可辨','残损')),
              ordinal INTEGER NOT NULL,
              occurrences INTEGER NOT NULL DEFAULT 1,
              current_text TEXT NOT NULL DEFAULT '',
              disposition TEXT NOT NULL DEFAULT 'pending' CHECK(disposition IN ('confirmed','pending','explained')),
              handling_note TEXT NOT NULL DEFAULT '',
              note_by INTEGER REFERENCES users(id),
              noted_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(passage_id,witness_id,mark,ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_gaps_work ON gaps(work_id,witness_id);
            """
        )
        self.conn.commit()

    def seed_demo(self) -> None:
        if self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        owner = self.add_user("项目负责人", "owner")
        editor = self.add_user("校勘编辑", "editor")
        work = self.create_work("一则残卷", "演示不同版本的校勘", owner)
        w1 = self.add_witness(work, "甲本", "version", "馆藏胶片", "")
        w2 = self.add_witness(work, "乙本", "fragment", "残片转录", "第二句残损")
        self.grant_witness_editor(w2, editor, owner)
        passage = self.add_passage(work, "第1节", "春水东流，故人南去。", owner)
        self.align_passage(passage, w1, "春水东流，故人南去。", 1, owner)
        self.align_passage(passage, w2, "春水东流，[不可辨][不可辨]。", 2, owner)
        variant = self.create_variant(passage, w2, "春水东流，故人南去。", "综合语义与行款补足", owner, 0)
        self.add_note(variant, "补字仍需参照纸背墨迹。", editor)
        gap = self.conn.execute("SELECT id FROM gaps WHERE passage_id=? AND witness_id=?", (passage, w2)).fetchone()
        self.set_gap_handling(int(gap["id"]), "pending", "两处不可辨待核胶片后补字。", owner)

    def add_user(self, name: str, role: str) -> int:
        if not name.strip() or role not in {"owner", "editor", "reviewer"}:
            raise DomainError("用户名或角色无效")
        with self.transaction():
            try:
                cur = self.conn.execute("INSERT INTO users(name,role) VALUES(?,?)", (name.strip(), role))
            except sqlite3.IntegrityError as exc:
                raise DomainError("用户名已存在") from exc
        return int(cur.lastrowid)

    def create_work(self, title: str, description: str, owner_id: int) -> int:
        owner = self.conn.execute("SELECT role FROM users WHERE id=?", (owner_id,)).fetchone()
        if not owner or owner["role"] != "owner" or not title.strip():
            raise DomainError("作品标题或负责人无效")
        with self.transaction():
            cur = self.conn.execute(
                "INSERT INTO works(title,description,owner_id,created_at) VALUES(?,?,?,?)",
                (title.strip(), description.strip(), owner_id, datetime.now().isoformat()),
            )
        return int(cur.lastrowid)

    def grant_work_access(self, work_id: int, user_id: int, permission: str, granted_by: int) -> None:
        if permission not in {"view", "review"}:
            raise DomainError("权限必须为 view 或 review")
        self._require_owner(work_id, granted_by)
        if not self.conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise DomainError("用户不存在")
        with self.transaction():
            self.conn.execute(
                "INSERT INTO work_access(work_id,user_id,permission) VALUES(?,?,?) "
                "ON CONFLICT(work_id,user_id) DO UPDATE SET permission=excluded.permission",
                (work_id, user_id, permission),
            )

    def _require_owner(self, work_id: int, user_id: int) -> None:
        row = self.conn.execute("SELECT 1 FROM works WHERE id=? AND owner_id=?", (work_id, user_id)).fetchone()
        if not row:
            raise DomainError("只有项目负责人可以执行此操作")

    def can_view_work(self, work_id: int, user_id: int) -> bool:
        return bool(self.conn.execute(
            "SELECT 1 FROM works WHERE id=? AND owner_id=? "
            "UNION ALL SELECT 1 FROM work_access WHERE work_id=? AND user_id=? "
            "UNION ALL SELECT 1 FROM witnesses w JOIN witness_editors e ON e.witness_id=w.id "
            "WHERE w.work_id=? AND e.user_id=? LIMIT 1",
            (work_id, user_id, work_id, user_id, work_id, user_id),
        ).fetchone())

    def can_edit_witness(self, witness_id: int, user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT w.work_id,wa.permission FROM witnesses w LEFT JOIN work_access wa ON wa.work_id=w.work_id AND wa.user_id=? WHERE w.id=?",
            (user_id, witness_id),
        ).fetchone()
        if not row:
            return False
        owner = self.conn.execute("SELECT 1 FROM works WHERE id=? AND owner_id=?", (row["work_id"], user_id)).fetchone()
        editor = self.conn.execute("SELECT 1 FROM witness_editors WHERE witness_id=? AND user_id=?", (witness_id, user_id)).fetchone()
        return bool(owner or editor)

    def add_witness(self, work_id: int, siglum: str, kind: str, source_note: str = "", missing_sections: str = "") -> int:
        if not self.conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
            raise DomainError("作品不存在")
        if not siglum.strip() or kind not in WITNESS_KINDS:
            raise DomainError("版本标识或类型无效")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO witnesses(work_id,siglum,kind,source_note,missing_sections,created_at) VALUES(?,?,?,?,?,?)",
                    (work_id, siglum.strip(), kind, source_note.strip(), missing_sections.strip(), datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("同一作品中的版本标识不能重复") from exc
        return int(cur.lastrowid)

    def grant_witness_editor(self, witness_id: int, user_id: int, granted_by: int) -> None:
        witness = self.conn.execute("SELECT work_id FROM witnesses WHERE id=?", (witness_id,)).fetchone()
        if not witness:
            raise DomainError("版本不存在")
        self._require_owner(witness["work_id"], granted_by)
        if not self.conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise DomainError("用户不存在")
        with self.transaction():
            self.conn.execute(
                "INSERT OR IGNORE INTO witness_editors(witness_id,user_id,granted_by) VALUES(?,?,?)",
                (witness_id, user_id, granted_by),
            )

    def add_passage(self, work_id: int, label: str, base_text: str, user_id: int) -> int:
        self._require_owner(work_id, user_id)
        text = validate_transcription(base_text)
        if not label.strip():
            raise DomainError("段落标签不能为空")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO passages(work_id,label,base_text,updated_by,updated_at) VALUES(?,?,?,?,?)",
                    (work_id, label.strip(), text, user_id, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("段落标签已存在") from exc
        return int(cur.lastrowid)

    def align_passage(self, passage_id: int, witness_id: int, aligned_text: str, sort_order: int, user_id: int) -> int:
        passage = self.conn.execute("SELECT * FROM passages WHERE id=?", (passage_id,)).fetchone()
        witness = self.conn.execute("SELECT * FROM witnesses WHERE id=?", (witness_id,)).fetchone()
        if not passage or not witness or passage["work_id"] != witness["work_id"]:
            raise DomainError("段落与版本不属于同一作品")
        if not self.can_edit_witness(witness_id, user_id):
            raise DomainError("无权编辑该版本")
        if sort_order <= 0:
            raise DomainError("排序号必须大于0")
        if passage["status"] == "locked" or self.conn.execute("SELECT 1 FROM passage_locks WHERE passage_id=?", (passage_id,)).fetchone():
            raise DomainError("段落已锁定，不能修改")
        text = validate_transcription(aligned_text)
        with self.transaction():
            row = self.conn.execute(
                "SELECT id FROM alignments WHERE passage_id=? AND witness_id=?", (passage_id, witness_id)
            ).fetchone()
            if row:
                alignment_id = int(row["id"])
                self.conn.execute(
                    "UPDATE alignments SET aligned_text=?,sort_order=? WHERE id=?",
                    (text, sort_order, alignment_id),
                )
            else:
                cur = self.conn.execute(
                    "INSERT INTO alignments(passage_id,witness_id,aligned_text,sort_order,created_by,created_at) VALUES(?,?,?,?,?,?)",
                    (passage_id, witness_id, text, sort_order, user_id, datetime.now().isoformat()),
                )
                alignment_id = int(cur.lastrowid)
            self._reconcile_gaps(passage["work_id"], passage_id, witness_id, text)
        return alignment_id

    @staticmethod
    def _mark_counts(text: str) -> dict:
        counts = {mark: 0 for mark in GAP_MARKS}
        for mark in _MARK_RE.findall(text):
            counts[mark] += 1
        return counts

    def _reconcile_gaps(self, work_id: int, passage_id: int, witness_id: int, aligned_text: str) -> None:
        """按 版本+段落+标记类型 对账缺口：同一处只更新出现次数，重复保存不累加。

        标记仍在则保留既有处理说明；标记消失则删除该条缺口。
        """
        now = datetime.now().isoformat()
        counts = self._mark_counts(aligned_text)
        for mark, count in counts.items():
            row = self.conn.execute(
                "SELECT id FROM gaps WHERE passage_id=? AND witness_id=? AND mark=?",
                (passage_id, witness_id, mark),
            ).fetchone()
            if count == 0:
                if row:
                    self.conn.execute("DELETE FROM gaps WHERE id=?", (row["id"],))
                continue
            if row:
                self.conn.execute(
                    "UPDATE gaps SET occurrences=?,current_text=?,updated_at=? WHERE id=?",
                    (count, aligned_text, now, row["id"]),
                )
            else:
                self.conn.execute(
                    "INSERT INTO gaps(work_id,passage_id,witness_id,mark,ordinal,occurrences,current_text,"
                    "disposition,handling_note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (work_id, passage_id, witness_id, mark, 1, count, aligned_text, "pending", "", now, now),
                )

    def set_gap_handling(self, gap_id: int, disposition: str, note: str, user_id: int) -> None:
        gap = self.conn.execute("SELECT * FROM gaps WHERE id=?", (gap_id,)).fetchone()
        if not gap:
            raise DomainError("缺口记录不存在")
        self._require_owner(gap["work_id"], user_id)
        if disposition not in GAP_STATUSES:
            raise DomainError("处理状态必须为 confirmed、pending 或 explained")
        note = note.strip()
        if not note:
            raise DomainError("处理说明不能为空")
        with self.transaction():
            self.conn.execute(
                "UPDATE gaps SET disposition=?,handling_note=?,note_by=?,noted_at=?,updated_at=? WHERE id=?",
                (disposition, note, user_id, datetime.now().isoformat(), datetime.now().isoformat(), gap_id),
            )

    def _latest_variants(self, work_id: int) -> dict:
        latest = {}
        for row in self.conn.execute(
            "SELECT v.* FROM variants v JOIN passages p ON p.id=v.passage_id "
            "WHERE p.work_id=? ORDER BY v.layer DESC,v.id DESC", (work_id,)
        ).fetchall():
            latest.setdefault((row["passage_id"], row["witness_id"]), dict(row))
        return latest

    def _gap_rows(self, work_id: int) -> list:
        rows = self.conn.execute(
            "SELECT g.*,p.label AS passage_label,w.siglum,w.kind,u.name AS note_by_name FROM gaps g "
            "JOIN passages p ON p.id=g.passage_id JOIN witnesses w ON w.id=g.witness_id "
            "LEFT JOIN users u ON u.id=g.note_by "
            "WHERE g.work_id=? ORDER BY w.id,p.id,g.mark", (work_id,)
        ).fetchall()
        latest = self._latest_variants(work_id)
        gaps = []
        for row in rows:
            item = dict(row)
            item["mark_label"] = f"[{row['mark']}]"
            item["disposition_label"] = GAP_STATUS_LABEL[row["disposition"]]
            item["handled"] = bool(row["handling_note"].strip())
            variant = latest.get((row["passage_id"], row["witness_id"]))
            item["current_revision"] = {
                "layer": variant["layer"],
                "proposed_text": variant["proposed_text"],
            } if variant else None
            gaps.append(item)
        return gaps

    def list_gaps(self, work_id: int, user_id: int) -> dict:
        if not self.can_view_work(work_id, user_id):
            raise DomainError("无权查看该校勘项目")
        gaps = self._gap_rows(work_id)
        witnesses = {}
        for row in self.conn.execute(
            "SELECT w.* FROM witnesses w WHERE w.work_id=? ORDER BY w.id", (work_id,)
        ).fetchall():
            witnesses[row["id"]] = {**dict(row), "gaps": [], "todo_count": 0, "occurrence_count": 0}
        passage_todo = {}
        for gap in gaps:
            bucket = witnesses.get(gap["witness_id"])
            if bucket is not None:
                bucket["gaps"].append(gap)
                bucket["occurrence_count"] += gap["occurrences"]
                if not gap["handled"]:
                    bucket["todo_count"] += 1
            passage_todo[gap["passage_id"]] = passage_todo.get(gap["passage_id"], 0) + (0 if gap["handled"] else 1)
        passages = []
        for row in self.conn.execute("SELECT id,label,status FROM passages WHERE work_id=? ORDER BY id", (work_id,)).fetchall():
            todo = passage_todo.get(row["id"], 0)
            passages.append({**dict(row), "gap_todo": todo, "finalizable": todo == 0})
        status_counts = {key: 0 for key in GAP_STATUSES}
        for gap in gaps:
            status_counts[gap["disposition"]] += 1
        return {
            "work_id": work_id,
            "witnesses": list(witnesses.values()),
            "passages": passages,
            "totals": {
                "gap_items": len(gaps),
                "occurrences": sum(g["occurrences"] for g in gaps),
                "todo_items": sum(1 for g in gaps if not g["handled"]),
                "by_disposition": status_counts,
            },
        }

    def create_variant(self, passage_id: int, witness_id: int, proposed_text: str, reason: str,
                       user_id: int, expected_revision: int) -> int:
        with self.transaction():
            passage, lock = self._editable_passage(passage_id, witness_id, user_id, expected_revision)
            text = validate_transcription(proposed_text)
            if len(reason.strip()) < 3:
                raise DomainError("取舍理由至少3个字符")
            if not self.conn.execute("SELECT 1 FROM alignments WHERE passage_id=? AND witness_id=?", (passage_id, witness_id)).fetchone():
                raise DomainError("该版本尚未对齐此段落")
            cur = self.conn.execute(
                "INSERT INTO variants(passage_id,witness_id,base_text,proposed_text,reason,created_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (passage_id, witness_id, passage["base_text"], text, reason.strip(), user_id, datetime.now().isoformat(), datetime.now().isoformat()),
            )
            variant_id = int(cur.lastrowid)
            revision = self._record_revision(passage_id, variant_id, 1, user_id)
            self.conn.execute("UPDATE passages SET revision=?,updated_by=?,updated_at=? WHERE id=?", (revision, user_id, datetime.now().isoformat(), passage_id))
        return variant_id

    def update_variant(self, variant_id: int, proposed_text: str, reason: str, user_id: int,
                       expected_revision: int) -> int:
        with self.transaction():
            variant = self.conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
            if not variant:
                raise DomainError("异文记录不存在")
            passage, _ = self._editable_passage(variant["passage_id"], variant["witness_id"], user_id, expected_revision)
            text = validate_transcription(proposed_text)
            if len(reason.strip()) < 3:
                raise DomainError("取舍理由至少3个字符")
            layer = int(self.conn.execute("SELECT COALESCE(MAX(layer),0)+1 FROM variants WHERE passage_id=? AND witness_id=?", (variant["passage_id"], variant["witness_id"])).fetchone()[0])
            self.conn.execute(
                "UPDATE variants SET proposed_text=?,reason=?,layer=?,updated_at=? WHERE id=?",
                (text, reason.strip(), layer, datetime.now().isoformat(), variant_id),
            )
            revision = self._record_revision(variant["passage_id"], variant_id, layer, user_id)
            self.conn.execute("UPDATE passages SET revision=?,updated_by=?,updated_at=? WHERE id=?", (revision, user_id, datetime.now().isoformat(), variant["passage_id"]))
        return revision

    def _editable_passage(self, passage_id: int, witness_id: int, user_id: int, expected_revision: int):
        passage = self.conn.execute("SELECT * FROM passages WHERE id=?", (passage_id,)).fetchone()
        witness = self.conn.execute("SELECT * FROM witnesses WHERE id=?", (witness_id,)).fetchone()
        if not passage or not witness or passage["work_id"] != witness["work_id"]:
            raise DomainError("段落与版本不属于同一作品")
        if passage["status"] == "locked" or self.conn.execute("SELECT 1 FROM passage_locks WHERE passage_id=?", (passage_id,)).fetchone():
            raise DomainError("段落已锁定，不能修改")
        if not self.can_edit_witness(witness_id, user_id):
            raise DomainError("无权编辑该版本")
        if passage["revision"] != expected_revision:
            raise DomainError(f"版本冲突：当前修订为 {passage['revision']}，提交基于 {expected_revision}")
        return passage, None

    def _record_revision(self, passage_id: int, variant_id: int, layer: int, user_id: int) -> int:
        revision = int(self.conn.execute("SELECT COALESCE(MAX(revision_no),0)+1 FROM revisions WHERE passage_id=?", (passage_id,)).fetchone()[0])
        snapshot = {
            "passage": dict(self.conn.execute("SELECT * FROM passages WHERE id=?", (passage_id,)).fetchone()),
            "variant": dict(self.conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()),
            "alignments": [dict(r) for r in self.conn.execute(
                "SELECT a.*,w.siglum,w.kind FROM alignments a JOIN witnesses w ON w.id=a.witness_id WHERE a.passage_id=? ORDER BY a.sort_order",
                (passage_id,),
            ).fetchall()],
        }
        self.conn.execute(
            "INSERT INTO revisions(passage_id,variant_id,revision_no,layer,snapshot_json,author_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (passage_id, variant_id, revision, layer, json.dumps(snapshot, ensure_ascii=False), user_id, datetime.now().isoformat()),
        )
        return revision

    def add_note(self, variant_id: int, body: str, author_id: int) -> int:
        variant = self.conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
        if not variant or not self.can_view_work(
            self.conn.execute("SELECT work_id FROM passages WHERE id=?", (variant["passage_id"],)).fetchone()["work_id"], author_id
        ):
            raise DomainError("异文不存在或无权评论")
        if not body.strip():
            raise DomainError("注释不能为空")
        with self.transaction():
            cur = self.conn.execute(
                "INSERT INTO notes(variant_id,body,author_id,created_at) VALUES(?,?,?,?)",
                (variant_id, body.strip(), author_id, datetime.now().isoformat()),
            )
        return int(cur.lastrowid)

    def lock_passage(self, passage_id: int, user_id: int, reason: str = "") -> None:
        passage = self.conn.execute("SELECT * FROM passages WHERE id=?", (passage_id,)).fetchone()
        if not passage:
            raise DomainError("段落不存在")
        self._require_owner(passage["work_id"], user_id)
        pending = self.conn.execute(
            "SELECT COUNT(*) FROM gaps WHERE passage_id=? AND (handling_note IS NULL OR handling_note='')",
            (passage_id,),
        ).fetchone()[0]
        if pending:
            raise DomainError(f"该段落还有 {pending} 条缺口未写处理说明，不能定稿")
        with self.transaction():
            self.conn.execute("UPDATE passages SET status='locked',updated_by=?,updated_at=? WHERE id=?", (user_id, datetime.now().isoformat(), passage_id))
            self.conn.execute(
                "INSERT OR REPLACE INTO passage_locks(passage_id,locked_by,reason,locked_at) VALUES(?,?,?,?)",
                (passage_id, user_id, reason.strip(), datetime.now().isoformat()),
            )

    def get_snapshot(self, passage_id: int, revision_no: int, user_id: int) -> dict:
        passage = self.conn.execute("SELECT work_id FROM passages WHERE id=?", (passage_id,)).fetchone()
        if not passage or not self.can_view_work(passage["work_id"], user_id):
            raise DomainError("无权查看该快照")
        row = self.conn.execute("SELECT * FROM revisions WHERE passage_id=? AND revision_no=?", (passage_id, revision_no)).fetchone()
        if not row:
            raise DomainError("快照不存在")
        return {"revision_no": row["revision_no"], "layer": row["layer"], "created_at": row["created_at"], "snapshot": json.loads(row["snapshot_json"])}

    def export_collation(self, work_id: int, user_id: int) -> dict:
        if not self.can_view_work(work_id, user_id):
            raise DomainError("无权查看该校勘项目")
        work = self.conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
        witnesses = [dict(r) for r in self.conn.execute("SELECT * FROM witnesses WHERE work_id=? ORDER BY id", (work_id,))]
        gaps = self._gap_rows(work_id)
        gaps_by_alignment = {}
        for gap in gaps:
            gaps_by_alignment.setdefault((gap["passage_id"], gap["witness_id"]), []).append(gap)
        passages = []
        for passage in self.conn.execute("SELECT * FROM passages WHERE work_id=? ORDER BY id", (work_id,)).fetchall():
            alignments = []
            for row in self.conn.execute(
                "SELECT a.*,w.siglum,w.kind,w.missing_sections FROM alignments a JOIN witnesses w ON w.id=a.witness_id "
                "WHERE a.passage_id=? ORDER BY a.sort_order", (passage["id"],)
            ).fetchall():
                item = dict(row)
                item["marks"] = [f"[{m}]" for m, c in self._mark_counts(item["aligned_text"]).items() for _ in range(c)]
                item["gap_list"] = gaps_by_alignment.get((passage["id"], item["witness_id"]), [])
                alignments.append(item)
            variants = []
            for row in self.conn.execute("SELECT * FROM variants WHERE passage_id=? ORDER BY witness_id,layer,id", (passage["id"],)).fetchall():
                variant = dict(row)
                variant["notes"] = [dict(r) for r in self.conn.execute("SELECT * FROM notes WHERE variant_id=? ORDER BY id", (row["id"],))]
                variants.append(variant)
            passage_gaps = [g for g in gaps if g["passage_id"] == passage["id"]]
            passages.append({
                **dict(passage),
                "alignments": alignments,
                "variants": variants,
                "gap_items": passage_gaps,
                "gap_todo": sum(1 for g in passage_gaps if not g["handled"]),
                "finalizable": all(g["handled"] for g in passage_gaps),
            })
        status_counts = {key: 0 for key in GAP_STATUSES}
        for gap in gaps:
            status_counts[gap["disposition"]] += 1
        gap_summary = {
            "total_items": len(gaps),
            "total_occurrences": sum(g["occurrences"] for g in gaps),
            "todo_items": sum(1 for g in gaps if not g["handled"]),
            "by_disposition": status_counts,
            "by_witness": [
                {
                    "witness_id": w["id"],
                    "siglum": w["siglum"],
                    "kind": w["kind"],
                    "gap_items": len(w["gaps"]),
                    "occurrences": w["occurrence_count"],
                    "todo_count": w["todo_count"],
                }
                for w in self.list_gaps(work_id, user_id)["witnesses"]
            ],
        }
        return {
            "work": dict(work),
            "witnesses": witnesses,
            "passages": passages,
            "gap_count": sum(g["occurrences"] for g in gaps),
            "gap_summary": gap_summary,
            "gaps": gaps,
        }

    def snapshot(self) -> dict:
        return {
            "users": [dict(r) for r in self.conn.execute("SELECT id,name,role FROM users ORDER BY id")],
            "works": [dict(r) for r in self.conn.execute("SELECT * FROM works ORDER BY id")],
            "witnesses": [dict(r) for r in self.conn.execute("SELECT * FROM witnesses ORDER BY id")],
            "passages": [dict(r) for r in self.conn.execute("SELECT * FROM passages ORDER BY id")],
        }
