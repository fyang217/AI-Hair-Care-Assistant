import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from agent import catalog, controller, local_parse
from rag import PROMPT, build_packet, validate_response
from storage import DB, build, evidence_record, records

FIXTURES = Path(__file__).with_name("test_fixtures")

def fixture_rows(name, key):
    bundle=json.loads((FIXTURES/name).read_text(encoding="utf-8"))
    if bundle["dataset_type"] != "synthetic":
        raise ValueError("测试数据集的来源类型无效")
    return bundle[key]

def simulated_packet(product_ids):
    """Standalone offline test adapter. Only tests import this module."""
    originals={}
    items=[]
    for pid in product_ids:
        entries=[]
        for review in fixture_rows("reviews.json","reviews"):
            if review["product_id"] != pid:
                continue
            eid=review["fixture_id"]
            data={k:review[k] for k in ("source_type","original_text")}
            originals[eid]=dict(kind="review",product_id=pid,data=data)
            entries.append(dict(evidence_id=eid,product_id=pid,kind="review",data=data))
        items.append(dict(product_id=pid,review_count=len(entries),reviews_sent=len(entries),
                          verified_price_available=False,evidence=entries))
    return dict(mode="simulation",eligible_products=items),originals

class ReviewSourceTests(unittest.TestCase):
    def test_rebuild_accepts_explicit_real_types_and_rejects_simulation(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            original, market, output=(root/name for name in ("original.xlsx","market.xlsx","next.sqlite"))
            source=Workbook()
            source.active.append(["other"])
            source.save(original)
            book=Workbook()
            book.active.title="ecommerce_reviews"
            book.active.append(["product_id","review_id","source_type","original_text"])
            book.active.append(["P012","fixture-import","marketplace","导入测试占位。"])
            book.save(market)
            build(original,market,output)
            self.assertEqual("marketplace",records(output,"P012")[0]["data"]["source_type"])
            book.active["C2"]="synthetic"
            book.save(market)
            rejected=root/"rejected.sqlite"
            with self.assertRaisesRegex(ValueError,"独立测试"):
                build(original,market,rejected)
            self.assertFalse(rejected.exists())
            self.assertFalse(rejected.with_suffix(".building").exists())

    def test_legacy_provenance_and_actual_counts(self):
        reviews=records(DB,kinds=("review",))
        self.assertEqual(10,len(reviews))
        self.assertEqual(6,sum(r["data"]["source_type"]=="official" for r in reviews))
        self.assertEqual(4,sum(r["data"]["source_type"]=="marketplace" for r in reviews))
        for row in reviews:
            self.assertEqual(row,evidence_record(f'E{row["id"]}'))

    def test_production_excludes_even_mislabeled_platform_and_unknown(self):
        synthetic=fixture_rows("reviews.json","reviews")[0]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"snapshot.sqlite"
            shutil.copyfile(DB,path)
            with closing(sqlite3.connect(path)) as db, db:
                ids=[]
                for index, source_type in enumerate(("synthetic","typo","marketplace"),1):
                    payload=dict(source_type=source_type,platform="品牌官网" if index!=3 else "京东",
                                 original_text=synthetic["original_text"] if index!=3 else "导入兼容性占位测试。",
                                 review_id=f"TEST-{index}")
                    cursor=db.execute("""INSERT INTO records(dataset,sheet,row_number,product_id,kind,state,payload)
                                        VALUES(?,?,?,?,?,?,?)""",
                                      ("future","ecommerce_reviews",index,"P012","review","仅临时测试",json.dumps(payload)))
                    ids.append(f'E{cursor.lastrowid}')
            packet,originals=build_packet("推荐 P012",local_parse("推荐 P012"),[catalog(path)["P012"]],path)
            self.assertEqual("production",packet["mode"])
            self.assertEqual(1,packet["eligible_products"][0]["review_count"])
            self.assertNotIn(synthetic["original_text"],json.dumps(packet,ensure_ascii=False))
            result=controller("推荐 P012",path)
            self.assertEqual(3,result["products"][0]["review_summary"]["count"])
            self.assertNotIn(synthetic["original_text"],json.dumps(result,ensure_ascii=False))
            self.assertTrue(all(evidence_record(eid,path) is None for eid in ids[:2]))
            self.assertEqual("marketplace",evidence_record(ids[2],path)["data"]["source_type"])

    def test_fixture_contract_and_annotations(self):
        reviews=fixture_rows("reviews.json","reviews")
        cases=fixture_rows("queries.json","cases")
        self.assertEqual(len(reviews),len({r["fixture_id"] for r in reviews}))
        self.assertEqual(len(cases),len({c["case_id"] for c in cases}))
        for row in reviews:
            self.assertEqual({"fixture_id","source_type","product_id","original_text","expected_annotation"},set(row))
            self.assertEqual("synthetic",row["source_type"])
            self.assertIn(row["product_id"],catalog())
        self.assertEqual({"正面","负面","中性","混合"},{r["expected_annotation"]["sentiment"] for r in reviews})
        self.assertGreaterEqual(len({c["expected"]["scalp"] for c in cases}),4)
        self.assertGreaterEqual(len({c["expected"]["hair"] for c in cases}),4)
        self.assertGreaterEqual(len({c["expected"]["budget_max"] for c in cases}),5)
        self.assertTrue(all(c["source_type"]=="synthetic" for c in cases))
        self.assertTrue(any("对比" in c["query"] for c in cases))

    def test_mock_simulation_is_not_real_consensus(self):
        packet, originals=simulated_packet(["P012","P014"])
        claims=[]
        for item in packet["eligible_products"]:
            entry=item["evidence"][0]
            claim=dict(text="该模拟样本描述洗后体验",evidence_id=entry["evidence_id"],
                       quote=entry["data"]["original_text"])
            claims.append(dict(product_id=item["product_id"],reason=claim,formula_analysis=None,
                               review_summary=dict(claim),suitable_needs=None,potential_limitations=None,unknowns=[]))
        reply={"recommendations":claims}
        with patch("rag.complete",return_value=(reply,"fixture")) as model:
            from rag import complete
            result,_=complete(PROMPT,packet)
            checked=validate_response(result,packet,originals)
            sent=model.call_args.args[1]
        self.assertEqual({"P012","P014"},set(checked))
        self.assertTrue(all(row["review_source_scope"]=="仅模拟测试" for row in checked.values()))
        self.assertEqual({"synthetic"},{e["data"]["source_type"] for item in sent["eligible_products"] for e in item["evidence"]})
        with self.assertRaisesRegex(ValueError,"来源"):
            validate_response(reply,dict(packet,mode="production"),originals)
        claims[0]["review_summary"]["text"]="真实消费者的共识是使用感受较好"
        with self.assertRaises(ValueError):
            validate_response(reply,packet,originals)
