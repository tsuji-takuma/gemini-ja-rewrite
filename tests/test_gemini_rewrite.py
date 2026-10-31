import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gemini_rewrite as gr  # noqa: E402


class MaskTest(unittest.TestCase):
    def test_round_trip(self):
        text = (
            "`npm install` を実行してから https://example.com/docs?a=1 を開きます。\n"
            "\n"
            "```bash\n"
            "echo hello\n"
            "```\n"
        )
        masked, mapping = gr.mask(text)
        self.assertNotIn("npm install", masked)
        self.assertNotIn("example.com", masked)
        self.assertNotIn("echo hello", masked)
        restored, missing, extra = gr.unmask(masked, mapping)
        self.assertEqual(restored, text)
        self.assertEqual(missing, [])
        self.assertEqual(extra, [])

    def test_url_stops_before_japanese(self):
        masked, mapping = gr.mask("詳しくは https://example.com/a を参照。")
        self.assertEqual(list(mapping.values()), ["https://example.com/a"])
        self.assertIn("を参照。", masked)

    def test_url_in_markdown_link(self):
        _, mapping = gr.mask("[公式](https://example.com/x)を見る")
        self.assertEqual(list(mapping.values()), ["https://example.com/x"])

    def test_same_segment_shares_token(self):
        masked, mapping = gr.mask("`a` と `a`")
        self.assertEqual(len(mapping), 1)
        self.assertEqual(masked.count("{{KEEP1}}"), 2)

    def test_detects_missing_and_unknown(self):
        _, mapping = gr.mask("`a` と `b`")
        _, missing, extra = gr.unmask("{{KEEP1}} と {{KEEP9}}", mapping)
        self.assertEqual(missing, ["{{KEEP2}}"])
        self.assertEqual(extra, ["{{KEEP9}}"])

    def test_apply_mask_to_previous(self):
        _, mapping = gr.mask("`foo` を使う")
        self.assertEqual(gr.apply_mask("まず `foo` を使います", mapping), "まず {{KEEP1}} を使います")


class EnvFileTest(unittest.TestCase):
    def write(self, directory, content):
        path = Path(directory) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_parses_only_target_key(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "# comment\nOTHER=1\nexport GEMINI_API_KEY=\"abc\"\n")
            self.assertEqual(gr.parse_env_file(path), "abc")

    def test_inline_comment_and_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gr.parse_env_file(self.write(d, "GEMINI_API_KEY=xyz # note\n")), "xyz")
            self.assertIsNone(gr.parse_env_file(self.write(d, "GEMINI_API_KEY=\n")))

    def test_searches_up_to_git_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            sub = root / "a" / "b"
            sub.mkdir(parents=True)
            (root / ".git").mkdir()
            env = self.write(root, "GEMINI_API_KEY=k\n")
            self.assertEqual(gr.find_env_file(sub), env.resolve())

    def test_does_not_climb_above_git_root(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "GEMINI_API_KEY=outside\n")
            root = Path(d) / "repo"
            (root / ".git").mkdir(parents=True)
            self.assertIsNone(gr.find_env_file(root))

    def test_outside_git_only_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "GEMINI_API_KEY=parent\n")
            child = Path(d) / "child"
            child.mkdir()
            with mock.patch.object(gr, "find_git_root", return_value=None):
                self.assertIsNone(gr.find_env_file(child))


class FindApiKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / ".git").mkdir()
        self.env = mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.dir / "config")}, clear=False
        )
        self.env.start()
        for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_CMD"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write_config_key(self, value, mode=0o600):
        path = self.dir / "config" / "gemini-ja-rewrite" / "api_key"
        path.parent.mkdir(parents=True)
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(mode)
        return path

    def test_env_var_wins(self):
        os.environ["GEMINI_API_KEY"] = "from-env"
        (self.dir / ".env").write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")
        key, source = gr.find_api_key(self.dir)
        self.assertEqual(key, "from-env")
        self.assertIn("環境変数", source)

    def test_dotenv_before_config_file(self):
        (self.dir / ".env").write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")
        self.write_config_key("from-file")
        key, _ = gr.find_api_key(self.dir)
        self.assertEqual(key, "from-dotenv")

    def test_command(self):
        os.environ["GEMINI_API_KEY_CMD"] = f"{sys.executable} -c \"print('from-cmd')\""
        self.write_config_key("from-file")
        key, source = gr.find_api_key(self.dir)
        self.assertEqual((key, source), ("from-cmd", "GEMINI_API_KEY_CMD"))

    def test_config_file_and_permission_warning(self):
        self.write_config_key("from-file", mode=0o644)
        warnings = []
        key, _ = gr.find_api_key(self.dir, warn=warnings.append)
        self.assertEqual(key, "from-file")
        self.assertTrue(any("chmod 600" in w for w in warnings))

    def test_not_found(self):
        self.assertEqual(gr.find_api_key(self.dir), (None, ""))


class PromptAndResponseTest(unittest.TestCase):
    def test_first_prompt(self):
        prompt = gr.build_user_prompt("原文です", intent="上司向け")
        self.assertIn("## 原文\n\n原文です", prompt)
        self.assertIn("## 書き手の意図\n\n上司向け", prompt)
        self.assertNotIn("前回の書き直し", prompt)

    def test_retry_prompt(self):
        prompt = gr.build_user_prompt("原文", previous="前回", feedback="- 限定が消えた")
        self.assertIn("## 前回の書き直し\n\n前回", prompt)
        self.assertIn("- 限定が消えた", prompt)
        self.assertIn("指摘された箇所だけ", prompt)

    def test_extract_skips_thoughts(self):
        response = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "考え中", "thought": True}, {"text": "本文"}]},
                    "finishReason": "STOP",
                }
            ]
        }
        self.assertEqual(gr.extract_text(response), ("本文", "STOP"))

    def test_extract_blocked(self):
        with self.assertRaises(gr.RewriteError):
            gr.extract_text({"promptFeedback": {"blockReason": "SAFETY"}})

    def test_strip_wrapping_fence(self):
        self.assertEqual(gr.strip_wrapping_fence("```markdown\n本文\n```\n"), "本文")
        self.assertEqual(gr.strip_wrapping_fence("本文\n```a\n```\n"), "本文\n```a\n```\n")


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.original = self.dir / "original.md"
        self.original.write_text("`make` を実行する。\n", encoding="utf-8")
        self.out = self.dir / "attempt1.md"
        self.env = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "dummy"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gr.main([*args])
        return code, out.getvalue(), err.getvalue()

    def fake_response(self, text):
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}

    def test_dry_run_does_not_call_api(self):
        with mock.patch.object(gr, "http_json") as http:
            code, out, _ = self.run_main("--original", str(self.original), "--dry-run")
        http.assert_not_called()
        self.assertEqual(code, gr.EXIT_OK)
        self.assertIn("{{KEEP1}} を実行する。", out)
        self.assertNotIn("dummy", out)

    def test_rewrite_restores_code(self):
        with mock.patch.object(gr, "http_json", return_value=self.fake_response("{{KEEP1}} を実行します。")) as http:
            code, _, err = self.run_main("--original", str(self.original), "--out", str(self.out))
        self.assertEqual(code, gr.EXIT_OK)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "`make` を実行します。\n")
        self.assertIn("gemini-3.8-flash", err)
        url = http.call_args[0][1]
        self.assertNotIn("dummy", url)

    def test_missing_placeholder_exit_code(self):
        with mock.patch.object(gr, "http_json", return_value=self.fake_response("実行します。")):
            code, _, err = self.run_main("--original", str(self.original), "--out", str(self.out))
        self.assertEqual(code, gr.EXIT_PLACEHOLDER)
        self.assertIn("`make`", err)

    def test_api_error_exit_code(self):
        with mock.patch.object(gr, "http_json", side_effect=gr.RewriteError("HTTP 400")):
            code, _, err = self.run_main("--original", str(self.original))
        self.assertEqual(code, gr.EXIT_API)
        self.assertIn("HTTP 400", err)

    def test_previous_requires_feedback(self):
        code, _, _ = self.run_main("--original", str(self.original), "--previous", str(self.original))
        self.assertEqual(code, gr.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
