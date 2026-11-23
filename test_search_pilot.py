import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

import search_pilot as pilot

class Response(io.BytesIO):
    status=200

class SearchPilotTests(unittest.TestCase):
    def test_queries_and_original_snapshot(self):
        self.assertEqual(20,len(pilot.QUERIES))
        for letter in "ABCDE":
            self.assertEqual(4,sum(qid.startswith(letter) for qid,_ in pilot.QUERIES))
        self.assertEqual(17,len(pilot.local_products(Path(__file__).with_name("data")/"haircare.sqlite")))

    @patch("search_pilot.build_opener")
    def test_contract_nonempty_empty_and_secret_safe_error(self, opener):
        perform=opener.return_value.open
        perform.return_value=Response(b'{"results":[{"title":"test"}]}')
        result=pilot.request_api("search",{"query":"测试","need_content":False},"fixture-token")
        req=perform.call_args.args[0]
        self.assertEqual(pilot.BASE+"search",req.full_url)
        self.assertEqual("POST",req.get_method())
        self.assertEqual(18,perform.call_args.kwargs["timeout"])
        self.assertTrue(result["billable"])
        perform.return_value=Response(b'{"content":""}')
        self.assertFalse(pilot.request_api("fetch",{"url":"https://example.org"},"fixture-token")["billable"])
        perform.side_effect=HTTPError(req.full_url,402,"fixture-token",{},None)
        failed=pilot.request_api("search",{"query":"测试"},"fixture-token")
        self.assertFalse(failed["billable"])
        self.assertNotIn("fixture-token",json.dumps(failed))

    def test_refuses_known_restricted_or_untrusted_links(self):
        for url in ("https://www.jd.com.evil.test/a","https://user:pw@jd.com/a",
                    "http://www.jd.com/a","https://passport.jd.com/a","https://127.0.0.1/a"):
            self.assertFalse(pilot.eligible_fetch(url),url)
        self.assertFalse(pilot.eligible_fetch("https://item.jd.com/1.html","你好，请登录"))
        self.assertTrue(pilot.eligible_fetch("https://item.jd.com/1.html"))

    @patch("search_pilot.request_api")
    def test_limits_and_no_replay(self, api):
        api.side_effect=lambda *_: dict(status=200,ms=1,billable=False,error="",data={"results":[]})
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            pilot.search(out,"fixture")
            self.assertEqual(20,api.call_count)
            with self.assertRaises(SystemExit):
                pilot.search(out,"fixture")
            self.assertEqual(20,api.call_count)
            selection=out/"select.json"
            pilot.write_json(selection,{"A01":[1,2,3]})
            with self.assertRaises(ValueError):
                pilot.fetch(out,"fixture",selection)
            self.assertEqual(20,api.call_count)

    @patch("search_pilot.request_api")
    def test_fetch_separates_original_content_from_search_summary(self, api):
        api.return_value=dict(status=200,ms=1,billable=True,error="",data={"title":"official","content":"真实页面字段"})
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            pilot.write_json(out/"search_capture.json",[dict(query_id="A01",results=[
                dict(url="https://item.jd.com/1.html",title="商品",summary="搜索摘要")])])
            selection=out/"select.json"
            pilot.write_json(selection,{"A01":[1]})
            pilot.fetch(out,"fixture",selection)
            self.assertEqual("真实页面字段",pilot.read_json(out/"pages/A01-1.json")["content"])
            api.assert_called_once_with("fetch",{"url":"https://item.jd.com/1.html"},"fixture")
