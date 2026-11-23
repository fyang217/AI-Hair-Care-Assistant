import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from agent import controller, parse
from deepseek import DEFAULT_MODEL

FIXTURE = dict(intent="评价", scalp=["易出油"], hair=["细软"], goals=["蓬松"],
               avoid=["香精"], budget_min=25, budget_max=120, strict_max=True,
               budget_unknown=False)

def reply(value, finish="stop"):
    return io.BytesIO(json.dumps({"choices":[{"finish_reason":finish,
        "message":{"content":value}}]}).encode())

class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{"DEEPSEEK_API_KEY":"test-token","DEEPSEEK_MODEL":"",
                                 "DEEPSEEK_BASE_URL":""})
        env.start()
        self.addCleanup(env.stop)

    @patch("deepseek.build_opener")
    def test_request_and_structured_fields(self, opener):
        opener.return_value.open.return_value=reply(json.dumps(FIXTURE))
        with patch("agent.local_parse",side_effect=AssertionError("本地解析不应执行")):
            actual=parse("给点意见",True)
        self.assertEqual(dict(FIXTURE,parser="DeepSeek",llm_model=DEFAULT_MODEL),actual)
        request=opener.return_value.open.call_args.args[0]
        self.assertEqual("https://api.deepseek.com/chat/completions",request.full_url)
        self.assertEqual("POST",request.get_method())
        self.assertEqual("Bearer test-token",request.get_header("Authorization"))
        self.assertEqual({"type":"json_object"},json.loads(request.data)["response_format"])

    @patch("deepseek.build_opener")
    def test_model_goals_drive_retrieval(self, opener):
        parsed=dict(FIXTURE,avoid=[],budget_max=None,budget_min=None,strict_max=False)
        opener.return_value.open.return_value=reply(json.dumps(parsed))
        result=controller("给点意见",use_llm=True)
        self.assertTrue(result["products"])
        self.assertEqual("评价",result["demand"]["intent"])
        self.assertEqual(["蓬松"],result["demand"]["goals"])
        self.assertEqual(1,opener.return_value.open.call_count)

    @patch("deepseek.build_opener")
    def test_bad_json_and_invalid_schema(self, opener):
        for body in ("   ","[]",json.dumps(dict(FIXTURE,budget_max=True))):
            with self.subTest(body=body):
                opener.return_value.open.return_value=reply(body)
                result=parse("控油100元以内",True)
                self.assertEqual("本地解析",result["parser"])
                self.assertEqual(100,result["budget_max"])
                self.assertIn("JSON",result["llm_error"])

    @patch("deepseek.build_opener")
    def test_network_http_and_truncation(self, opener):
        for error, clue in [(URLError("private-detail"),"网络"),(
            HTTPError("https://api.deepseek.com",401,"private-detail",{},None),"401")]:
            opener.return_value.open.side_effect=error
            result=parse("控油",True)
            self.assertIn(clue,result["llm_error"])
            self.assertNotIn("private-detail",result["llm_error"])
        opener.return_value.open.side_effect=None
        opener.return_value.open.return_value=reply(json.dumps(FIXTURE),"length")
        self.assertIn("未完整",parse("控油",True)["llm_error"])

    @patch("deepseek.build_opener")
    def test_config_and_no_call_in_local_mode(self, opener):
        self.assertEqual("本地解析",parse("蓬松")["parser"])
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":""}):
            self.assertIn("DEEPSEEK_API_KEY",parse("蓬松",True)["llm_error"])
        with patch.dict(os.environ,{"DEEPSEEK_BASE_URL":"https://example.org"}):
            self.assertIn("DEEPSEEK_BASE_URL",parse("蓬松",True)["llm_error"])
        opener.assert_not_called()

class LiveDeepSeekTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("DEEPSEEK_LIVE_TEST")=="1" and os.getenv("DEEPSEEK_API_KEY"),
                         "需要显式启用 DeepSeek 实网测试并配置密钥")
    def test_real_deepseek_request(self):
        result=parse("请对比两款洗发水。我头皮容易出油，头发细软，预算不超过120元，避开香精。",True)
        self.assertEqual("DeepSeek",result["parser"],result.get("llm_error"))
        self.assertEqual("对比",result["intent"])
        self.assertEqual(120,result["budget_max"])
        self.assertIn("香精",result["avoid"])

class DotenvTests(unittest.TestCase):
    def test_load_from_project_root_and_keep_explicit_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            project=root/"project"
            project.mkdir()
            (project/"deepseek.py").write_bytes(Path(__file__).with_name("deepseek.py").read_bytes())
            (project/".env").write_text("DEEPSEEK_API_KEY=fixture-file\n",encoding="utf-8")
            (root/".env").write_text("DEEPSEEK_API_KEY=fixture-other\n",encoding="utf-8")
            check=project/"check.py"
            check.write_text(
                'import os,sys,deepseek\n'
                'assert os.environ["DEEPSEEK_API_KEY"] == sys.argv[1]\n',
                encoding="utf-8",
            )
            env=dict(os.environ)
            env.pop("DEEPSEEK_API_KEY",None)
            env.pop("PYTHON_DOTENV_DISABLED",None)
            for explicit, expected in [(None,"fixture-file"),("fixture-environment","fixture-environment")]:
                if explicit:
                    env["DEEPSEEK_API_KEY"]=explicit
                result=subprocess.run([sys.executable,str(check),expected],cwd=root,
                                      env=env,capture_output=True,timeout=10)
                self.assertEqual(0,result.returncode)
