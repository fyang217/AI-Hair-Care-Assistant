import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from agent import feedback
from deepseek import DeepSeekError
import test_evidence_regression as exact
from test_deepseek import FIXTURE
from test_rag import recommendation
from test_web_tools import HIT, PAGE, QUERY, event, web_reply

APP=Path(__file__).parent/"app.py"

class StreamlitTests(unittest.TestCase):
    def start(self):
        return AppTest.from_file(str(APP),default_timeout=15).run()

    def test_19_recommend_and_evidence(self):
        app=self.start()
        app.text_area[0].set_value("P014")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn("蜂花控油蓬松洗发露",[s.value for s in app.subheader])
        self.assertIn("配方及备案",[s.label for s in app.expander])
        self.assertIn("官网及电商资料",[s.label for s in app.expander])

    def test_20_candidate_budget(self):
        app=self.start()
        app.text_area[0].set_value("蜂花不超过50元")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn("价格待核候选",[s.value for s in app.subheader])

    def test_21_compare(self):
        app=self.start()
        app.multiselect[0].set_value(["P014","P009"]).run()
        self.assertFalse(app.exception)
        self.assertEqual(2,len(app.subheader))

    def test_22_feedback(self):
        app=self.start()
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/"feedback.sqlite"
            with patch("agent.feedback",side_effect=lambda msg, rating: feedback(msg,rating,database)):
                app.text_area[1].set_value("希望看到购买规格")
                app.button[1].click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.success)
            db=sqlite3.connect(database)
            try:
                self.assertEqual([(3,"希望看到购买规格")],db.execute("SELECT rating,message FROM feedback").fetchall())
            finally:
                db.close()

    def test_23_repeated_review_source(self):
        app=self.start()
        app.text_area[0].set_value("P012评价")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn("评价证据（2 条）",[s.label for s in app.expander])

    def test_24_deepseek_success_status(self):
        app=self.start()
        with patch("agent.llm",return_value=(FIXTURE,"deepseek-flash")) as api:
            app.text_area[0].set_value("给点意见")
            app.checkbox[0].check()
            app.button[0].click().run()
            api.assert_called_once()
        self.assertFalse(app.exception)
        self.assertIn("DeepSeek API 已成功",app.success[0].value)
        self.assertIn("120",app.json[0].value)

    def test_25_deepseek_missing_key_status(self):
        app=self.start()
        with patch.dict("os.environ",{"DEEPSEEK_API_KEY":""}):
            app.text_area[0].set_value("P014")
            app.checkbox[0].check()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn("已回退到本地解析",app.warning[0].value)

    def test_26_rag_analysis_and_original_evidence(self):
        app=self.start()
        parsed=dict(FIXTURE,intent="推荐",avoid=[],budget_min=None,budget_max=None)
        with patch("agent.llm",return_value=(parsed,"deepseek-flash")), patch(
                "rag.complete",side_effect=lambda system,packet,**kw:(recommendation(packet),"deepseek-flash")):
            app.text_area[0].set_value("推荐 P012")
            app.checkbox[0].check()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("本次传入有原文的评价 0 条" in c.value for c in app.caption))
        self.assertIn("评价证据（2 条）",[e.label for e in app.expander])
        self.assertTrue(any("原始证据 E" in e.label for e in app.expander))
        self.assertTrue(any("模型解读，待核实" in e.value for e in app.text))

    def test_27_web_call_trace_and_sources(self):
        app=self.start()
        parsed=dict(FIXTURE,intent="推荐",goals=["控油"],avoid=[],budget_min=None,budget_max=None)
        with patch.dict("os.environ",{"DEEPSEEK_API_KEY":"fixture"}), patch(
                "agent.llm",return_value=(parsed,"fixture")), patch(
                "web_tools.complete",return_value=({"search":True,"query":"草本净凉洗发乳 台湾 官网",
                    "terms":["草本净凉洗发乳"]},"fixture")), patch(
                "web_tools.request_api",side_effect=[event({"results":[HIT]}),event(PAGE)]), patch(
                "rag.complete",side_effect=web_reply):
            app.text_area[0].set_value(QUERY)
            app.checkbox[0].check()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any(e.label=="本次工具调用与费用" for e in app.expander))
        self.assertTrue(any(e.label=="外部原始证据 W1a" for e in app.expander))
        self.assertTrue(any("fetch · ok" in e.value for e in app.text))

    def test_28_exact_query_partial_validation_keeps_external_and_local(self):
        app=self.start()
        parsed=dict(FIXTURE,intent="推荐",goals=["控油","去屑"],avoid=[],budget_min=None,budget_max=None)
        search={"results":[dict(url=exact.URL,title=exact.NAME,snippet=exact.POSITION)]}
        mixed=exact.reply(potential_limitations=exact.record("保证控油48小时"))
        with patch.dict("os.environ",{"DEEPSEEK_API_KEY":"fixture"}), patch(
                "agent.llm",return_value=(parsed,"fixture")), patch(
                "web_tools.complete",return_value=({"search":True,"query":"海飞丝 清爽控油型 洗发水",
                    "terms":["海飞丝"]},"fixture")), patch(
                "web_tools.request_api",side_effect=[event(search),event(exact.PAGE)]), patch(
                "rag.complete",return_value=(mixed,"fixture")):
            app.text_area[0].set_value(exact.QUERY)
            app.checkbox[0].check()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn(exact.NAME,[s.value for s in app.subheader])
        self.assertTrue(any("本地 SQLite" in e.value for e in app.caption))
        self.assertTrue(any("数值未逐字" in e.value for e in app.text))
        self.assertTrue(any("W1a" in e.value and exact.POSITION in e.value for e in app.text))

    def test_29_full_generation_failure_keeps_page_disclosures(self):
        app=self.start()
        parsed=dict(FIXTURE,intent="推荐",goals=["控油"],avoid=[],budget_min=None,budget_max=None)
        search={"results":[dict(url=exact.URL,title=exact.NAME,snippet=exact.POSITION)]}
        with patch.dict("os.environ",{"DEEPSEEK_API_KEY":"fixture"}), patch(
                "agent.llm",return_value=(parsed,"fixture")), patch(
                "web_tools.complete",return_value=({"search":True,"query":"海飞丝 清爽控油型 洗发水",
                    "terms":["海飞丝"]},"fixture")), patch(
                "web_tools.request_api",side_effect=[event(search),event(exact.PAGE)]), patch(
                "rag.complete",side_effect=DeepSeekError("fixture")):
            app.text_area[0].set_value(exact.QUERY)
            app.checkbox[0].check()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("取得的外部资料" in e.value for e in app.warning))
        self.assertTrue(any("无硅油配方 + 柠檬精粹" in e.value for e in app.text))
        self.assertTrue(any(e.label=="外部原始证据 W1a" for e in app.expander))
