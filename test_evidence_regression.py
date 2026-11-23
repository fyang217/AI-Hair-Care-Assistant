import unittest
from unittest.mock import patch

from agent import catalog, local_parse
from rag import build_packet, recommend, validate_response
from web_tools import external_product

QUERY="请联网推荐中国大陆海飞丝去屑洗发水清爽控油型，重点分析油性头皮是否适用，并提供配方、产品定位和来源证据。"
URL="https://www.head-shoulders.com.cn/product-detail-roots-oil.html"
NAME="海飞丝去屑洗发水-清爽控油型"
POSITION="针对油性头皮的头屑"
DURATION="72 小时头皮胺去屑⁵，1 洗就去屑⁴，一净一护⁸"
PAGE=dict(title=NAME,content=f"# {NAME}\n\n{POSITION}\n\n#### 核心成分\n\n无硅油配方 + 柠檬精粹\n\n#### 功效验证\n\n{DURATION}")

def record(text, quote=POSITION, eid="W1a"):
    return dict(text=text,quote=quote,evidence_id=eid)

def reply(**fields):
    response=dict(product_id="WEB1",reason=record("官方定位与油性头皮的去屑目标相符"),
                  formula_analysis=None,review_summary=None,suitable_needs=None,
                  potential_limitations=None,unknowns=[])
    response.update(fields)
    return {"recommendations":[response]}

class EvidenceGenerationRegression(unittest.TestCase):
    def setUp(self):
        self.external,_=external_product(URL,PAGE,["海飞丝"],["控油"],1,[],NAME)
        self.demand=local_parse(QUERY)
        self.packet,self.originals=build_packet(QUERY,self.demand,[self.external,catalog()["P012"]])

    def test_exact_official_numbers_and_explicit_gap(self):
        output=reply(potential_limitations=record(DURATION,DURATION),
                     unknowns=["价格待核","没有可核实的消费者评价"])
        claims=validate_response(output,self.packet,self.originals)["WEB1"]
        self.assertEqual("品牌官方宣称",claims["claims"]["potential_limitations"]["status"])
        self.assertEqual("模型解读，待核实",claims["claims"]["reason"]["status"])
        self.assertIn("价格待核",claims["unknowns"])
        self.assertIn("没有可核实的消费者评价",claims["unknowns"])

    def test_grounded_limitation_can_mention_multiple_unknowns(self):
        for text in (
            "现有证据为品牌官网正文，且记录提示具体销售版本、SKU 对应关系和价格待核验；因此不能确认实际在售包装。",
            "官网宣传含功效数字，但本次证据未见完整配方及真实消费者评价，无法独立验证效果。"
        ):
            output=reply(potential_limitations=record(text))
            claims=validate_response(output,self.packet,self.originals)["WEB1"]["claims"]
            self.assertIn("potential_limitations",claims)

    def test_unsupported_quantities_are_not_written_as_fact(self):
        with self.assertRaisesRegex(ValueError,"数值"):
            validate_response(reply(reason=record("适用于所有发质且48小时有效")),self.packet,self.originals)

    def test_source_sensitive_disclosures_are_still_rejected(self):
        for invented in ("售价五十元","预算符合","价格待核，符合预算","消费者反馈很好",
                         "使用者反映头皮舒适","存在已购记录","备案号已核实",
                         "事实来源 https://invalid.example.com/fake"):
            with self.subTest(statement=invented),self.assertRaises(ValueError):
                validate_response(reply(reason=record(invented)),self.packet,self.originals)

    def test_missing_and_cross_product_citations(self):
        local_eid=next(eid for eid,r in self.originals.items() if r["product_id"]=="P012")
        for eid in ("W999",local_eid):
            with self.subTest(evidence=eid),self.assertRaisesRegex(ValueError,"引用无效"):
                validate_response(reply(reason=record("油性头皮可参考此定位",eid=eid)),
                                  self.packet,self.originals)

    def test_partial_formula_matches_actual_official_anchor(self):
        output=reply(formula_analysis=record("这只是官网披露的部分配方信息","无硅油配方 + 柠檬精粹"))
        item=validate_response(output,self.packet,self.originals)["WEB1"]
        self.assertEqual("品牌官方宣称",item["claims"]["formula_analysis"]["basis_type"])
        self.assertTrue(any("部分成分" in gap for gap in self.external["gaps"]))

    @patch("rag.complete")
    def test_bad_optional_clause_does_not_discard_external_analysis(self, generator):
        generator.return_value=(reply(potential_limitations=record("承诺控油48小时"),
                                     review_summary=record("消费者反馈不错")),"fixture")
        outcome=recommend(QUERY,self.demand,[self.external])
        self.assertEqual("partial",outcome["status"])
        self.assertEqual({"reason"},set(outcome["analyses"]["WEB1"]["claims"]))
        self.assertEqual({"potential_limitations","review_summary"},{r["field"] for r in outcome["rejected"]})

    @patch("rag.complete")
    def test_all_invalid_keeps_rejection_reason(self, generator):
        generator.return_value=(reply(reason=record("售价五十元")),"fixture")
        outcome=recommend(QUERY,self.demand,[self.external])
        self.assertEqual("fallback",outcome["status"])
        self.assertIn("外部资料",outcome["message"])
        self.assertIn("报价",outcome["rejected"][0]["reason"])
