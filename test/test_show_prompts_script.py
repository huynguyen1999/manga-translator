import io
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devscripts.show_prompts import (
    SAMPLE_PAGES,
    SAMPLE_STORY_GUIDE,
    build_story_summary_prompt_data,
    build_synopsis_prompt_data,
    build_translation_chunk_prompt_data,
    load_pages_from_dir,
    main,
    render_markdown,
    render_pretty,
)


class TestShowPromptsScript(unittest.TestCase):
    def test_build_synopsis_prompt_data(self):
        data = build_synopsis_prompt_data(SAMPLE_PAGES, target_lang="ENG", merge=False)
        self.assertIn("Manga Synopsis Prompt", data["title"])
        self.assertIn("Create a detailed, spoiler-inclusive synopsis", data["system_prompt"])
        self.assertIn("OVERVIEW", data["system_prompt"])
        self.assertIn("[Page 001]", data["user_prompt"])
        self.assertIn("おい、聞いたか？昨日の転校生の話…", data["user_prompt"])
        self.assertIn("[Page 002]", data["user_prompt"])
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["messages"][0]["role"], "system")
        self.assertEqual(data["messages"][1]["role"], "user")

    def test_build_synopsis_merge_prompt_data(self):
        data = build_synopsis_prompt_data(SAMPLE_PAGES, target_lang="ENG", merge=True)
        self.assertTrue(data["is_merge_consolidation"])
        self.assertIn("Combine the supplied partial summaries into one spoiler-inclusive synopsis", data["system_prompt"])

    def test_build_story_summary_prompt_data(self):
        data = build_story_summary_prompt_data(SAMPLE_PAGES, manual_ranges=[(1, 2)], auto_within_manual=False)
        self.assertIn("Manga Small Summary for Translation", data["title"])
        self.assertIn("Japanese manga story analyst", data["system_prompt"])
        self.assertIn("Use these exact story ranges: [(1, 2)]", data["user_prompt"])
        self.assertIn("[PAGE 1]", data["user_prompt"])
        self.assertIn("[PANEL p1_01 | PANEL ORDER 1]", data["user_prompt"])
        self.assertIn("consolidation_prompt", data)
        self.assertIn("Consolidate these ordered manga analysis windows", data["consolidation_prompt"])

    def test_build_translation_chunk_prompt_data(self):
        data = build_translation_chunk_prompt_data(SAMPLE_PAGES, chunk_size=2)
        self.assertIn("Manga Translation Chunk Prompt", data["title"])
        self.assertIn("senior Japanese-to-English manga localization professional", data["system_prompt"])
        self.assertIn("STORY SUMMARY AND LOCALIZATION GUIDE:", data["user_prompt"])
        self.assertIn("IMMEDIATELY PRECEDING CHUNK TRANSCRIPT:", data["user_prompt"])
        self.assertIn("CURRENT PAGES:", data["user_prompt"])
        self.assertIn("r1", data["user_prompt"])
        self.assertEqual(data["chunk_size"], 2)

    def test_render_pretty(self):
        data = build_synopsis_prompt_data(SAMPLE_PAGES)
        rendered = render_pretty(data, enabled=False)
        self.assertIn("Manga Synopsis Prompt", rendered)
        self.assertIn("SYSTEM PROMPT", rendered)
        self.assertIn("USER PROMPT", rendered)

    def test_render_markdown(self):
        data = build_story_summary_prompt_data(SAMPLE_PAGES)
        rendered = render_markdown(data)
        self.assertIn("# Manga Small Summary for Translation", rendered)
        self.assertIn("## System Prompt", rendered)
        self.assertIn("## User Prompt", rendered)
        self.assertIn("## Multi-Window Consolidation Prompt", rendered)

    def test_cli_execution_all_prompts_json(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--prompt", "all", "--format", "json"])
        self.assertEqual(code, 0)
        output = json.loads(buf.getvalue())
        self.assertIn("prompts", output)
        self.assertEqual(len(output["prompts"]), 3)
        titles = [p["title"] for p in output["prompts"]]
        self.assertTrue(any("Synopsis" in t for t in titles))
        self.assertTrue(any("Summary" in t for t in titles))
        self.assertTrue(any("Translation Chunk" in t for t in titles))

    def test_cli_execution_single_prompt(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--prompt", "chunk", "--chunk-size", "1", "--format", "markdown"])
        self.assertEqual(code, 0)
        content = buf.getvalue()
        self.assertIn("# Manga Translation Chunk Prompt", content)
        self.assertNotIn("# Manga Synopsis Prompt", content)

    def test_cli_file_output(self, tmp_path_factory=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "test_out.md"
            code = main(["--prompt", "synopsis", "--format", "markdown", "--output", str(out_file)])
            self.assertEqual(code, 0)
            self.assertTrue(out_file.is_file())
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("# Manga Synopsis Prompt", content)


if __name__ == "__main__":
    unittest.main()
