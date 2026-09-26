import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import CollationDB, DomainError

class GapListTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=CollationDB(self.path)
        self.owner=self.db.add_user("负责人","owner"); self.editor=self.db.add_user("编辑","editor"); self.outsider=self.db.add_user("外部","reviewer")
        self.work=self.db.create_work("残卷","缺口清单",self.owner)
        self.w1=self.db.add_witness(self.work,"甲本","version"); self.w2=self.db.add_witness(self.work,"乙本","fragment")
        self.db.grant_witness_editor(self.w2,self.editor,self.owner)
        self.p1=self.db.add_passage(self.work,"第一节","春水东流，故人南去。",self.owner)
        self.p2=self.db.add_passage(self.work,"第二节","山高月小，水落石出。",self.owner)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def _items(self,witness_id=None):
        data=self.db.get_gap_list(self.work,self.owner)
        return [it for w in data["witnesses"] for it in w["items"] if witness_id is None or it["witness_id"]==witness_id]
    def test_three_markers_and_no_accumulation_on_resave(self):
        self.db.align_passage(self.p1,self.w2,"春水[缺页]东流，[不可辨][不可辨]。",1,self.editor)
        items=self._items()
        self.assertEqual(2,len(items))
        by={g["marker"]:g for g in items}
        self.assertEqual(1,by["[缺页]"]["occurrence_count"])
        self.assertEqual(2,by["[不可辨]"]["occurrence_count"])
        self.assertEqual(1,by["[缺页]"]["revision"])
        self.db.align_passage(self.p1,self.w2,"春水[缺页]东流，[不可辨][不可辨]。",1,self.editor)
        items=self._items()
        self.assertEqual(2,len(items),"重复保存不能累加缺口条目")
        by={g["marker"]:g for g in items}
        self.assertEqual(2,by["[不可辨]"]["occurrence_count"])
        self.assertEqual(2,by["[不可辨]"]["revision"])
        self.assertEqual(2,by["[缺页]"]["revision"])
    def test_realign_replaces_markers_and_keeps_disposition(self):
        self.db.align_passage(self.p1,self.w2,"春水[缺页]东流，[残损]。",1,self.editor)
        gap=[g for g in self._items() if g["marker"]=="[缺页]"][0]
        self.db.set_gap_disposition(gap["id"],self.owner,"confirmed","胶片此处整叶缺失")
        self.db.align_passage(self.p1,self.w2,"春水[缺页]东流，[不可辨]。",1,self.editor)
        items=self._items()
        self.assertEqual(2,len(items))
        by={g["marker"]:g for g in items}
        self.assertNotIn("[残损]",by,"重对齐后消失的标记应移出清单")
        self.assertEqual("confirmed",by["[缺页]"]["status"])
        self.assertEqual("胶片此处整叶缺失",by["[缺页]"]["disposition_note"])
        self.assertEqual(2,by["[缺页]"]["revision"])
        self.assertEqual("pending",by["[不可辨]"]["status"])
    def test_disposition_rules_and_lock_gate(self):
        self.db.align_passage(self.p1,self.w2,"春水[缺页]东流。",1,self.editor)
        self.db.align_passage(self.p2,self.w2,"山高[不可辨]小。",2,self.editor)
        g1=[g for g in self._items() if g["passage_id"]==self.p1][0]
        g2=[g for g in self._items() if g["passage_id"]==self.p2][0]
        with self.assertRaisesRegex(DomainError,"负责人"):
            self.db.set_gap_disposition(g1["id"],self.editor,"explained","越权填写")
        with self.assertRaisesRegex(DomainError,"说明"):
            self.db.set_gap_disposition(g1["id"],self.owner,"confirmed","")
        with self.assertRaisesRegex(DomainError,"状态"):
            self.db.set_gap_disposition(g1["id"],self.owner,"unknown","无效状态")
        with self.assertRaisesRegex(DomainError,"定稿"):
            self.db.lock_passage(self.p1,self.owner,"定稿")
        self.db.set_gap_disposition(g1["id"],self.owner,"据实缺失","乙本此处缺叶")
        self.db.lock_passage(self.p1,self.owner,"定稿")
        with self.assertRaisesRegex(DomainError,"定稿"):
            self.db.lock_passage(self.p2,self.owner,"定稿")
        self.db.set_gap_disposition(g2["id"],self.owner,"已说明","墨迹漶漫，存疑不补")
        self.db.lock_passage(self.p2,self.owner,"定稿")
    def test_gap_list_groups_by_witness_and_export_carries_status(self):
        self.db.align_passage(self.p1,self.w1,"春水东流，故人南去。",1,self.owner)
        self.db.align_passage(self.p1,self.w2,"春水[缺页]东流，[残损]。",2,self.editor)
        self.db.align_passage(self.p2,self.w2,"[不可辨]高月小。",1,self.editor)
        data=self.db.get_gap_list(self.work,self.owner)
        self.assertEqual(3,data["total_todo"])
        by_siglum={w["siglum"]:w for w in data["witnesses"]}
        self.assertEqual(0,by_siglum["甲本"]["todo"])
        self.assertEqual(3,by_siglum["乙本"]["todo"])
        gap=[g for g in self._items() if g["marker"]=="[缺页]"][0]
        self.db.set_gap_disposition(gap["id"],self.owner,"confirmed","缺叶属实")
        data=self.db.get_gap_list(self.work,self.owner)
        self.assertEqual(2,data["total_todo"])
        exported=self.db.export_collation(self.work,self.owner)
        self.assertEqual(3,exported["gap_count"])
        self.assertEqual({"items":3,"occurrences":3,"pending":2,"confirmed":1,"explained":0},exported["gap_summary"])
        p1_gaps=exported["passages"][0]["gaps"]
        self.assertEqual(2,len(p1_gaps))
        confirmed=[g for g in p1_gaps if g["status"]=="confirmed"][0]
        self.assertEqual("据实缺失",confirmed["status_label"])
        self.assertEqual("缺叶属实",confirmed["disposition_note"])
        with self.assertRaisesRegex(DomainError,"无权"):
            self.db.get_gap_list(self.work,self.outsider)

if __name__=="__main__": unittest.main()
