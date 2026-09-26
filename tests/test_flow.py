import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import CollationDB, DomainError

class CollationFlowTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=CollationDB(self.path)
        self.owner=self.db.add_user("负责人","owner"); self.editor=self.db.add_user("编辑","editor"); self.reviewer=self.db.add_user("审阅","reviewer"); self.outsider=self.db.add_user("外部","reviewer")
        self.work=self.db.create_work("残卷","异文比较",self.owner)
        self.w1=self.db.add_witness(self.work,"甲本","version"); self.w2=self.db.add_witness(self.work,"乙本","fragment","馆藏残片","中段缺页")
        self.db.grant_witness_editor(self.w2,self.editor,self.owner); self.db.grant_work_access(self.work,self.reviewer,"view",self.owner)
        self.passage=self.db.add_passage(self.work,"第一节","春水东流，故人南去。",self.owner)
        self.db.align_passage(self.passage,self.w1,"春水东流，故人南去。",1,self.owner)
        self.db.align_passage(self.passage,self.w2,"春水东流，[缺页]",2,self.editor)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def test_multilayer_revision_snapshot_export_and_lock(self):
        variant=self.db.create_variant(self.passage,self.w2,"春水东流，故人南去。","按语义补足",self.editor,0)
        rev=self.db.update_variant(variant,"春水东流，[不可辨]人南去。","墨迹受损，不再直接补写",self.editor,1)
        self.assertEqual(2,rev)
        snap=self.db.get_snapshot(self.passage,2,self.owner)
        self.assertEqual(2,snap["layer"])
        exported=self.db.export_collation(self.work,self.reviewer)
        self.assertEqual(1,exported["gap_count"])
        self.assertEqual(1,exported["gap_summary"]["todo_items"])
        self.assertTrue(exported["passages"][0]["variants"][0]["notes"] == [])
        with self.assertRaisesRegex(DomainError,"未写处理说明"):
            self.db.lock_passage(self.passage,self.owner,"定稿")
        gaps=self.db.list_gaps(self.work,self.owner)["witnesses"][1]["gaps"]
        self.db.set_gap_handling(gaps[0]["id"],"confirmed","胶片比对确为缺页",self.owner)
        self.db.lock_passage(self.passage,self.owner,"定稿")
        with self.assertRaisesRegex(DomainError,"锁定"):
            self.db.update_variant(variant,"另一文本","无意义修改",self.editor,2)
    def test_optimistic_lock_permission_and_mark_validation(self):
        first=self.db.create_variant(self.passage,self.w2,"补足一","理由一",self.editor,0)
        with self.assertRaisesRegex(DomainError,"版本冲突"):
            self.db.create_variant(self.passage,self.w2,"补足二","理由二",self.editor,0)
        with self.assertRaisesRegex(DomainError,"无权"):
            self.db.create_variant(self.passage,self.w2,"补足三","理由三",self.reviewer,1)
        with self.assertRaisesRegex(DomainError,"无权"):
            self.db.export_collation(self.work,self.outsider)
        with self.assertRaisesRegex(DomainError,"括号"):
            self.db.align_passage(self.passage,self.w1,"文本[未闭合",9,self.owner)
    def test_realign_gap_idempotent_and_statuses(self):
        # 重复保存同一对齐不累加缺口
        self.db.align_passage(self.passage,self.w2,"春水[缺页]，[残损][残损]。",2,self.editor)
        listing=self.db.list_gaps(self.work,self.owner)
        bucket=next(w for w in listing["witnesses"] if w["id"]==self.w2)
        self.assertEqual(2,bucket["todo_count"])
        by_mark={g["mark"]:g for g in bucket["gaps"]}
        self.assertEqual(1,by_mark["缺页"]["occurrences"])
        self.assertEqual(2,by_mark["残损"]["occurrences"])
        # 再次保存次数不翻倍
        self.db.align_passage(self.passage,self.w2,"春水[缺页]，[残损][残损]。",2,self.editor)
        bucket=next(w for w in self.db.list_gaps(self.work,self.owner)["witnesses"] if w["id"]==self.w2)
        self.assertEqual({("缺页",1),("残损",2)},{(g["mark"],g["occurrences"]) for g in bucket["gaps"]})
        # 负责人写处理说明：据实缺失 / 待补 / 已说明
        self.db.set_gap_handling(by_mark["缺页"]["id"],"confirmed","对照底本确为脱页",self.owner)
        self.db.set_gap_handling(by_mark["残损"]["id"],"explained","残损字据他本校勘说明",self.owner)
        exported=self.db.export_collation(self.work,self.owner)
        self.assertEqual(0,exported["gap_summary"]["todo_items"])
        self.assertEqual({"confirmed":1,"pending":0,"explained":1},exported["gap_summary"]["by_disposition"])
        # 非负责人不能写处理说明
        with self.assertRaisesRegex(DomainError,"负责人"):
            self.db.set_gap_handling(by_mark["缺页"]["id"],"pending","编辑越权",self.editor)
        with self.assertRaisesRegex(DomainError,"处理说明不能为空"):
            self.db.set_gap_handling(by_mark["缺页"]["id"],"confirmed","  ",self.owner)
        # 重新对齐后标记消失，缺口删除；仍在的标记保留处理说明
        self.db.align_passage(self.passage,self.w2,"春水东流，[缺页]",2,self.editor)
        bucket=next(w for w in self.db.list_gaps(self.work,self.owner)["witnesses"] if w["id"]==self.w2)
        remaining={g["mark"]:g for g in bucket["gaps"]}
        self.assertEqual({"缺页"},set(remaining))
        self.assertEqual("confirmed",remaining["缺页"]["disposition"])
        self.assertEqual("对照底本确为脱页",remaining["缺页"]["handling_note"])
        # 待补缺口未处理时不能定稿
        self.db.align_passage(self.passage,self.w2,"[不可辨]",2,self.editor)
        with self.assertRaisesRegex(DomainError,"未写处理说明"):
            self.db.lock_passage(self.passage,self.owner,"定稿")

if __name__=="__main__": unittest.main()
