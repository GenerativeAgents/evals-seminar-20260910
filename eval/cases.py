"""Dataset rows and DeepEval test cases for slide evaluations."""

import html as html_lib
import re
import urllib.request
from collections.abc import Iterable

from deepeval.test_case import LLMTestCase, ToolCall

PAPER_IDS = ("1706.03762", "2512.07828", "2603.03303")
MAX_SOURCE_CHARS = 120_000
DEFAULT_EXPECTED_TOOLS = ("execute", "generate_pptx")


def fetch_paper_text(arxiv_id: str) -> str:
    """Fetch and normalize a fixed evaluation source from ar5iv."""
    url = f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sd36-eval)"})
    html = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "ignore")
    html = re.sub(
        r'(?is)<math[^>]*?alttext="([^"]*)"[^>]*>.*?</math>',
        lambda match: f" {html_lib.unescape(match.group(1))} ",
        html,
    )
    html = re.sub(r"(?is)<(script|style|math|nav|header|footer).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    references = list(re.finditer(r"\bReferences\b", text))
    if references and references[-1].start() > len(text) * 0.5:
        text = text[: references[-1].start()]
    return text[:MAX_SOURCE_CHARS]


def build_dataset_rows() -> list[dict]:
    """Fetch all rows before publishing, so a partial Dataset is never created."""
    rows = []
    for arxiv_id in PAPER_IDS:
        rows.append(
            {
                "arxiv_id": arxiv_id,
                "paper_url": f"https://arxiv.org/abs/{arxiv_id}",
                "source_text": fetch_paper_text(arxiv_id),
                "expected_tools": list(DEFAULT_EXPECTED_TOOLS),
            }
        )
    return rows


def slides_to_text(data: dict) -> str:
    """Convert slide JSON to the text representation used by DeepEval."""
    parts = [f"# {data['title']}"]
    for index, slide in enumerate(data["slides"], 1):
        if slide["type"] == "title":
            continue
        parts.append(f"## Slide {index} [{slide['type']}] {slide.get('title', '')}")
        if slide.get("subtitle"):
            parts.append(slide["subtitle"])
        for bullet in slide.get("bullets") or []:
            parts.append(f"- {bullet}")
    return "\n".join(parts)


def build_slide_test_case(
    *,
    output: dict,
    source_text: str,
    expected_tools: Iterable[str],
) -> LLMTestCase:
    """Build the common single-turn case consumed by all three metrics."""
    slides_text = output.get("slides_text")
    if not isinstance(slides_text, str) or not slides_text:
        slides = output.get("slides")
        if isinstance(slides, dict):
            slides_text = slides_to_text(slides)
    if not isinstance(slides_text, str) or not slides_text:
        raise ValueError("Agent output does not contain evaluable slide text.")

    tool_calls = output.get("tool_calls")
    if not isinstance(tool_calls, list):
        raise ValueError("Agent output tool_calls must be a list.")
    return LLMTestCase(
        input=source_text,
        actual_output=slides_text,
        tools_called=[ToolCall(name=call["name"]) for call in tool_calls],
        expected_tools=[ToolCall(name=name) for name in expected_tools],
    )
