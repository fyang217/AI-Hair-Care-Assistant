import json
import os
import unittest
from unittest.mock import patch

from agent import controller, local_parse
from deepseek import DeepSeekError
from rag import build_packet, validate_response
from web_tools import external_product, page_market, select_pages, selection_gap

URL="https://tw.loccitane.com/products/fresh-shampoo"
NAME="草本淨涼洗髮乳"
PAGE=dict(title=NAME+"｜L'OCCITANE",content=f"""# {NAME}
適合油性頭皮，洗後讓髮根保持清爽柔順。
## 成分表
AQUA, GLYCERIN, CITRIC ACID, MENTHOL, PANTHENOL, GLYCOL, TOCOPHEROL, SODIUM BENZOATE
顧客評論：原文實際屬於補充包
定價 NT$1,580
""")
HIT={"url":URL,"title":PAGE["title"],"snippet":"油性头皮洗护"}
QUERY="请联网推荐中国台湾的草本净凉洗发乳，适合容易出油的发质"

def event(data, error="", status=200, billable=True):
    return dict(data=data,error=error,status=status,billable=billable,ms=11)

def web_reply(system, packet, **kwargs):
    item=next(p for p in packet["eligible_products"] if p["product_id"].startswith("WEB"))
    official=next(r for r in item["evidence"] if r["kind"]=="official")
    claim=dict(text="根据官方介绍，该款面向油性头皮",evidence_id=official["evidence_id"],
               quote=NAME)
    return {"recommendations":[dict(product_id=item["product_id"],reason=claim,formula_analysis=None,
                                    review_summary=None,suitable_needs=None,potential_limitations=None,
                                    unknowns=[])]},"fixture-model"

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{"DEEPSEEK_API_KEY":"fixture"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.router=patch("web_tools.complete",return_value=(
            dict(search=True,query="欧舒丹 草本净凉洗发乳 台湾 官网",terms=["草本净凉洗发乳"]),"fixture-model"))
        self.route=self.router.start()
        self.addCleanup(self.router.stop)

    def parse_for(self, query):
        return patch("agent.llm",return_value=(local_parse(query),"fixture-model"))

    def test_local_adequate_and_local_opt_out(self):
        with self.parse_for("推荐 P012"), patch("rag.complete",side_effect=DeepSeekError("fixture")), patch("web_tools.request_api") as api:
            result=controller("推荐 P012",use_llm=True,allow_web=True)
            self.assertEqual("P012",result["products"][0]["id"])
            self.assertEqual("skipped",result["web"]["tools"][-1]["status"])
            controller("只查本地，请联网推荐外部产品",use_llm=True,allow_web=True)
        self.route.assert_not_called()
        api.assert_not_called()

    def test_search_fetch_joint_evidence_and_trace(self):
        noisy=dict(HIT,url="https://www.vichy-me.com/other",title="薇姿控油洗发水",snippet="无关")
        with self.parse_for(QUERY), patch("web_tools.request_api",side_effect=[
                event({"results":[noisy,HIT]}),event(PAGE)]) as api, patch(
                "rag.complete",side_effect=web_reply) as generate:
            result=controller(QUERY,use_llm=True,allow_web=True)
        self.assertEqual(["search","fetch"],[c.args[0] for c in api.call_args_list])
        self.assertTrue(all(c.kwargs["timeout"]<=18 for c in api.call_args_list))
        self.assertEqual(URL,api.call_args_list[1].args[1]["url"])
        candidate=result["web"]["candidates"][0]
        self.assertEqual("中国台湾",candidate["market"])
        claim=candidate["ai_analysis"]["claims"]["reason"]
        self.assertEqual(URL,claim["record"]["url"])
        self.assertEqual("W1a",claim["evidence_id"])
        packet=generate.call_args.args[1]
        self.assertTrue(any(r["evidence_id"].startswith("E") for item in packet["eligible_products"] for r in item["evidence"]))
        self.assertTrue(any(r["evidence_id"].startswith("W") for item in packet["eligible_products"] for r in item["evidence"]))
        self.assertNotIn("原文實際屬於補充包",json.dumps(packet,ensure_ascii=False))
        self.assertAlmostEqual(.00150,result["web"]["cost_usd"])

    def test_insufficient_local_quality_can_trigger_search_without_explicit_web_request(self):
        query="欧舒丹草本净凉洗发乳适合油性头皮吗"
        parsed=dict(local_parse(query),product_queries=["草本净凉洗发乳"])
        with patch("agent.llm",return_value=(parsed,"fixture-model")), patch(
                "web_tools.request_api",return_value=event({"results":[]},billable=False)) as api, patch(
                "rag.complete",side_effect=DeepSeekError("fixture")):
            controller(query,use_llm=True,allow_web=True)
        api.assert_called_once()
        self.route.assert_called_once()

    def test_rejects_off_topic_in_search_and_mismatched_fetch(self):
        self.assertEqual([],select_pages([dict(HIT,title="无关洗发水",snippet="油性")],["草本净凉洗发乳"],["控油"]))
        item, issue=external_product(URL,dict(PAGE,content="# 其他品牌控油洗发水\n适合油性头皮"),
                                     ["草本净凉洗发乳"],["控油"],1,[],HIT["title"])
        self.assertIsNone(item)
        self.assertIn("不一致",issue)

    def test_market_budget_and_sku_guard(self):
        candidate,_=external_product(URL,PAGE,["草本净凉洗发乳"],["控油"],1,[],HIT["title"])
        query="中国大陆 推荐控油洗发水"
        self.assertEqual([],build_packet(query,local_parse(query),[candidate])[0]["eligible_products"])
        budget_query="中国台湾 推荐控油洗发水 不超过80元"
        candidate["evidence"].append(dict(candidate["evidence"][0],kind="review",id="W1c",
                                          data={"source_type":"marketplace","original_text":"未匹配 SKU 的假样本"}))
        self.assertEqual([],build_packet(budget_query,local_parse(budget_query),[candidate])[0]["eligible_products"])
        packet,_=build_packet(QUERY,local_parse(QUERY),[candidate])
        self.assertTrue(packet["eligible_products"])
        self.assertFalse(any(r["kind"]=="review" for r in packet["eligible_products"][0]["evidence"]))
        self.assertEqual("市场待核",page_market("https://item.jd.com/100.html"))

    def test_explicit_size_and_sku_must_match_visible_evidence(self):
        name="海飞丝清爽控油洗发水500ml"
        url="https://item.jd.com/100123456.html"
        body=f"# {name}\n商品编号：100123456\n适合油性头皮洗护\n"
        candidate,_=external_product(url,dict(title=name,content=body),[],["控油"],1,[],name)
        self.assertEqual("500ml",candidate["specification"])
        self.assertEqual("100123456",candidate["sku"])
        self.assertFalse(selection_gap("找500毫升 SKU 100123456",candidate))
        self.assertIn("规格",selection_gap("找750毫升 SKU 100123456",candidate))
        self.assertIn("SKU",selection_gap("找500ml SKU 100123457",candidate))
        wrong_label=body.replace("100123456","100654321")
        wrong,_=external_product(url,dict(title=name,content=wrong_label),[],["控油"],1,[],name)
        self.assertIsNone(wrong["sku"])

    def test_cross_product_or_fabricated_source_fails(self):
        candidate,_=external_product(URL,PAGE,[],["控油"],1,[],HIT["title"])
        packet,originals=build_packet(QUERY,local_parse(QUERY),[candidate])
        reply,_=web_reply("",packet)
        reply["recommendations"][0]["reason"]["evidence_id"]="E999"
        with self.assertRaisesRegex(ValueError,"引用无效"):
            validate_response(reply,packet,originals)

    def test_search_failure_retains_local(self):
        with self.parse_for(QUERY+" P012"), patch("web_tools.request_api",return_value=event(
                {},error="网络超时",status=None,billable=None)), patch(
                "rag.complete",side_effect=DeepSeekError("fixture")):
            result=controller(QUERY+" P012",use_llm=True,allow_web=True)
        self.assertEqual("P012",result["products"][0]["id"])
        self.assertEqual("failed",result["web"]["tools"][-1]["status"])
        self.assertEqual(1,result["web"]["unknown_billing"])

    def test_fetch_is_limited_to_two_even_with_many_results(self):
        hits=[dict(HIT,url=f"https://tw.loccitane.com/products/page-{i}") for i in range(7)]
        with self.parse_for(QUERY), patch("web_tools.request_api",side_effect=[
                event({"results":hits}),event(PAGE),event(PAGE)]) as api, patch(
                "rag.complete",side_effect=web_reply):
            controller(QUERY,use_llm=True,allow_web=True)
        self.assertEqual(3,api.call_count)

class LiveWebToolTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("HAIRCARE_WEB_LIVE_TEST")=="1" and os.getenv("DEEPSEEK_API_KEY"),
                         "需要显式启用实网验收并配置现有密钥")
    def test_controller_live_request(self):
        query="请联网推荐中国大陆海飞丝去屑洗发水清爽控油型，参考品牌官网，重点看油性头皮是否适用。"
        result=controller(query,use_llm=True,allow_web=True)
        web=result["web"]
        print(json.dumps({"tools":web["tools"],"tool_cost_usd":web["cost_usd"],
                          "model_usage":web["model_usage"],"rag_status":(result.get("rag") or {}).get("status"),
                          "rag_message":(result.get("rag") or {}).get("message"),
                          "candidates":[{"name":c["name"],"market":c["market"],
                              "references":[v["evidence_id"] for v in (c.get("ai_analysis") or {}).get("claims",{}).values()]}
                              for c in web["candidates"]]},ensure_ascii=False))
        self.assertEqual("DeepSeek",result["demand"]["parser"],result["demand"].get("llm_error"))
        self.assertTrue(any(e["tool"]=="search" and e["status"]=="ok" for e in web["tools"]),web["tools"])
        self.assertTrue(any(e["tool"]=="fetch" and e["status"]=="ok" for e in web["tools"]),web["tools"])
        self.assertTrue(any(c["market"]=="中国大陆" and c.get("ai_analysis") for c in web["candidates"]),result["rag"])
