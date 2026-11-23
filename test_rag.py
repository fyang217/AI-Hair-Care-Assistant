import io
import json
import unittest
from unittest.mock import patch

from agent import catalog, controller, local_parse
from deepseek import DeepSeekError
from rag import build_packet, recommend, validate_response, verified_quote
from storage import DB, evidence_record, records

def recommendation(packet, product_id="P012"):
    item = next(p for p in packet["eligible_products"] if p["product_id"] == product_id)
    formula = next(e for e in item["evidence"] if e["kind"] == "formula")
    review = next((e for e in item["evidence"] if e["kind"] == "review"), None)
    f = dict(text="需要结合该版本成分表核对选择", evidence_id=formula["evidence_id"],
             quote=formula["data"]["ingredients_as_published"][:24])
    feedback = (dict(text="已收录体验可作为选择时的参考", evidence_id=review["evidence_id"],
                    quote=review["data"]["original_text"][:24]) if review else None)
    return {"recommendations":[dict(product_id=product_id, reason=f, formula_analysis=f,
                                    review_summary=feedback, suitable_needs=None,
                                    potential_limitations=None, unknowns=[])]}

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.products = catalog()
        self.demand = local_parse("护发")
        self.packet, self.originals = build_packet("护发",self.demand,
                                                   [self.products["P012"],self.products["P014"]])

    def test_retrieval_and_provenance(self):
        rows = records(DB,"P012",("formula","review"))
        self.assertEqual({"formula","review"}, {r["kind"] for r in rows})
        self.assertEqual(3,len(rows))
        self.assertEqual(len(self.originals),len(set(self.originals)))
        for eid, source in self.originals.items():
            self.assertEqual(source,evidence_record(eid))
        self.assertIsNone(evidence_record("E999999999"))
        self.assertIsNone(evidence_record("E1 OR 1=1"))

    @patch("deepseek.build_opener")
    def test_formula_price_and_metadata_reach_client_without_review_text(self, api):
        def receive(request, *, timeout):
            self.assertEqual("https://api.deepseek.com/chat/completions", request.full_url)
            packet=json.loads(json.loads(request.data)["messages"][1]["content"])
            by_id={p["product_id"]:p for p in packet["eligible_products"]}
            self.assertEqual(0,by_id["P012"]["review_count"])
            by_kind={e["kind"]:e for e in by_id["P012"]["evidence"]}
            self.assertIn("ingredients_as_published",by_kind["formula"]["data"])
            self.assertNotIn("review",by_kind)
            self.assertIn("introduction",by_kind["official"]["data"])
            price=next(e for e in by_id["P014"]["evidence"] if e["kind"]=="price")
            self.assertFalse(price["verified_for_request"])
            self.assertIn("amount",price["data"])
            return io.BytesIO(json.dumps({"choices":[{"finish_reason":"stop",
                "message":{"content":json.dumps(recommendation(packet))}}]}).encode())
        api.return_value.open.side_effect=receive
        with patch.dict("os.environ",{"DEEPSEEK_API_KEY":"fixture","DEEPSEEK_BASE_URL":"","DEEPSEEK_MODEL":""}):
            result=recommend("护发",self.demand,[self.products["P012"],self.products["P014"]])
        self.assertEqual("ok",result["status"])
        api.assert_called_once()
        actual=result["analyses"]["P012"]
        self.assertEqual(0,actual["review_count"])
        self.assertEqual("模型解读，待核实",actual["claims"]["reason"]["status"])
        self.assertIn("待核验",actual["version_notice"])

    def test_fabricated_id(self):
        response=recommendation(self.packet)
        response["recommendations"][0]["reason"]["evidence_id"]="E999999"
        with self.assertRaisesRegex(ValueError,"引用无效"):
            validate_response(response,self.packet,self.originals)

    def test_cross_product_citation(self):
        response=recommendation(self.packet)
        foreign=next(e for e,r in self.originals.items() if r["product_id"]=="P014" and r["kind"]=="formula")
        response["recommendations"][0]["reason"]["evidence_id"]=foreign
        with self.assertRaisesRegex(ValueError,"跨产品"):
            validate_response(response,self.packet,self.originals)

    def test_valid_id_does_not_validate_made_up_content(self):
        for field, value in (("quote","编造的原文片段"),("text","售价50元并符合预算")):
            response=recommendation(self.packet)
            response["recommendations"][0]["reason"][field]=value
            with self.assertRaises(ValueError):
                validate_response(response,self.packet,self.originals)

    def test_review_summary_cannot_cite_formula(self):
        response=recommendation(self.packet)
        rec=response["recommendations"][0]
        rec["review_summary"]=rec["formula_analysis"]
        with self.assertRaisesRegex(ValueError,"证据类型"):
            validate_response(response,self.packet,self.originals)

    def test_missing_price_and_review(self):
        result=validate_response(recommendation(self.packet,"P014"),self.packet,self.originals)["P014"]
        self.assertEqual(0,result["review_count"])
        self.assertNotIn("review_summary",result["claims"])
        self.assertTrue(any("报价" in item for item in result["unknowns"]))

    @patch("rag.complete")
    def test_budget_filters_and_rejects_ineligible_sku(self, api):
        demand=local_parse("预算不超过50元")
        self.assertEqual("insufficient",recommend("护发",demand,[self.products["P014"]])["status"])
        api.assert_not_called()
        allowed_packet,_=build_packet("护发",demand,[self.products["P014"]])
        with self.assertRaises(ValueError):
            validate_response(recommendation(self.packet,"P014"),allowed_packet,{})

    def test_independent_quote_sku_currency_and_budget(self):
        price=dict(kind="price",product_id="P014",url="https://example.com/p",
                   observed_at="2026-11-17 10:00:00 UTC+08:00",data=dict(
                    amount=50,currency="CNY",price_type="核实售价",platform="京东",
                    item_id="a",specification="750ml"))
        listing=dict(kind="listing",product_id="P014",state="同款规格已核验",
                     data=dict(platform="京东",item_id="a",specification="750 毫升"))
        self.assertTrue(verified_quote(price,[listing],local_parse("不超过50元")))
        self.assertFalse(verified_quote(price,[listing],local_parse("低于50元")))
        listing["state"]="待核验"
        self.assertFalse(verified_quote(price,[listing],local_parse("不超过50元")))
        listing["state"]="同款规格已核验"
        listing["data"]["specification"]="700ml"
        self.assertFalse(verified_quote(price,[listing],local_parse("不超过50元")))

    @patch("rag.complete",side_effect=DeepSeekError("模拟网络失败"))
    @patch("agent.llm",return_value=(local_parse("推荐 P012"),"deepseek-flash"))
    def test_generation_failure_preserves_local_recommendation(self, parser, api):
        result=controller("推荐 P012",use_llm=True)
        self.assertEqual("fallback",result["rag"]["status"])
        self.assertEqual("P012",result["products"][0]["id"])
        self.assertIsNone(result["products"][0].get("ai_analysis"))

    @patch("rag.complete")
    def test_disabled_and_no_key_fallback(self, api):
        self.assertIsNone(controller("推荐 P014")["rag"])
        with patch.dict("os.environ",{"DEEPSEEK_API_KEY":""}):
            result=controller("推荐 P014",use_llm=True)
        self.assertIn("DEEPSEEK_API_KEY",result["demand"]["llm_error"])
        self.assertEqual("P014",result["products"][0]["id"])
        api.assert_not_called()

    @patch("rag.complete")
    @patch("agent.llm",return_value=(local_parse("推荐 P012"),"deepseek-flash"))
    def test_controller_supplies_ai_and_fallback_results(self, parser, api):
        api.side_effect=lambda system,packet,**kwargs:(recommendation(packet),"deepseek-flash")
        result=controller("推荐 P012",use_llm=True)
        self.assertEqual("ok",result["rag"]["status"])
        self.assertIn("formula_analysis",result["products"][0]["ai_analysis"]["claims"])
        self.assertNotIn("review_summary",result["products"][0]["ai_analysis"]["claims"])
        self.assertTrue(result["products"][0]["formula"]["data"]["ingredients_as_published"])
