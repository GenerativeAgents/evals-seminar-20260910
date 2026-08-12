import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cases import build_slide_test_case, slides_to_text  # noqa: E402


class CasesTest(unittest.TestCase):
    def test_slides_to_text_excludes_title_slide(self):
        text = slides_to_text(
            {
                "title": "Paper",
                "slides": [
                    {"type": "title", "title": "Paper", "subtitle": "Author"},
                    {"type": "content", "title": "Claim", "bullets": ["Fact"]},
                ],
            }
        )
        self.assertEqual(text, "# Paper\n## Slide 2 [content] Claim\n- Fact")

    def test_build_slide_test_case_maps_tools(self):
        case = build_slide_test_case(
            output={
                "slides_text": "# Paper",
                "tool_calls": [{"name": "execute"}, {"name": "generate_pptx"}],
            },
            source_text="source",
            expected_tools=["execute", "generate_pptx"],
        )
        self.assertEqual(case.actual_output, "# Paper")
        self.assertEqual([tool.name for tool in case.tools_called], ["execute", "generate_pptx"])
        self.assertEqual([tool.name for tool in case.expected_tools], ["execute", "generate_pptx"])


if __name__ == "__main__":
    unittest.main()
