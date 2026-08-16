from __future__ import annotations

import base64
import html
import json
import os
import sys
from typing import Any

import weave
from dotenv import load_dotenv


PPTX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _debug(message: str) -> None:
    if os.environ.get("PRESENTATION_TRACE_DEBUG") == "1":
        print(f"[presentation-trace] {message}", file=sys.stderr, flush=True)


def _render_slide(slide: dict[str, Any], index: int) -> str:
    slide_type = slide.get("type", "content")
    title = html.escape(str(slide.get("title", "")))
    subtitle = html.escape(str(slide.get("subtitle", "")))
    bullets = slide.get("bullets") or []
    bullet_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in bullets)

    if slide_type == "title":
        body = f"<h1>{title}</h1>"
        if subtitle:
            body += f"<p class=\"subtitle\">{subtitle}</p>"
    elif slide_type == "section":
        body = f"<h2>{title}</h2>"
    else:
        body = f"<h2>{title}</h2><div class=\"rule\"></div>"
        if bullet_html:
            body += f"<ul>{bullet_html}</ul>"

    return (
        f'<section class="slide {html.escape(slide_type)}">'
        f'<span class="number">{index}</span>{body}</section>'
    )


def _render_html(slide_data: dict[str, Any]) -> str:
    title = html.escape(str(slide_data.get("title", "Presentation")))
    slides = "".join(
        _render_slide(slide, index)
        for index, slide in enumerate(slide_data.get("slides") or [], start=1)
    )
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; font-family: Inter, "Noto Sans JP", sans-serif; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px; background: #e9eef5; color: #333; }}
  .deck {{ display: grid; gap: 28px; max-width: 1120px; margin: 0 auto; }}
  .slide {{ position: relative; aspect-ratio: 16 / 9; padding: 6% 7%; overflow: hidden;
            background: white; border-radius: 12px; box-shadow: 0 10px 28px #1b2a4a22; }}
  .slide.title {{ display: flex; flex-direction: column; justify-content: center;
                  background: #1b2a4a; color: white; }}
  .slide.section {{ display: flex; align-items: center; background: #2e86ab; color: white; }}
  h1 {{ margin: 0; font-size: clamp(34px, 5vw, 64px); line-height: 1.15; }}
  h2 {{ margin: 0; font-size: clamp(28px, 4vw, 52px); line-height: 1.2; color: #1b2a4a; }}
  .section h2 {{ color: white; }}
  .subtitle {{ margin-top: 24px; color: #c5d0dd; font-size: clamp(18px, 2.2vw, 30px); }}
  .rule {{ height: 5px; margin: 22px 0 28px; background: #2e86ab; }}
  ul {{ margin: 0; padding-left: 1.35em; font-size: clamp(18px, 2vw, 29px); line-height: 1.55; }}
  li + li {{ margin-top: .45em; }}
  .number {{ position: absolute; right: 22px; bottom: 16px; color: #789; font-size: 14px; }}
  .title .number, .section .number {{ color: #ffffffaa; }}
</style>
</head>
<body><main class="deck">{slides}</main></body>
</html>"""


def main() -> None:
    load_dotenv()
    _debug("reading payload")
    payload = json.load(sys.stdin)
    project_path = payload["projectPath"]
    pptx_bytes = base64.b64decode(payload["pptxBase64"], validate=True)
    slide_data = payload["slideData"]
    metadata = payload["metadata"]
    file_name = metadata["fileName"]
    html_preview = _render_html(slide_data)

    _debug("initializing Weave")
    client = weave.init(project_path)
    _debug("Weave initialized")

    @weave.op(name="trace_presentation")
    def trace_presentation() -> weave.Content:
        weave.set_view(
            "slides",
            html_preview,
            extension="html",
            mimetype="text/html",
            metadata={"title": slide_data.get("title", "Presentation")},
        )
        return weave.Content.from_bytes(
            pptx_bytes,
            extension="pptx",
            mimetype=PPTX_MIMETYPE,
            metadata={
                "filename": file_name,
                "title": slide_data.get("title", "Presentation"),
                "slide_count": len(slide_data.get("slides") or []),
                "presentation_trace_id": metadata["presentationTraceId"],
            },
        )

    attributes = {
        "conversation_id": metadata["conversationId"],
        "variant": metadata["variant"],
        "event_type": "presentation_content",
        "presentation_trace_id": metadata["presentationTraceId"],
        "file_name": file_name,
        "file_size_bytes": len(pptx_bytes),
        "slide_count": len(slide_data.get("slides") or []),
        "source": "slide-generator-ui",
    }
    try:
        _debug("uploading PPTX and HTML view")
        with weave.attributes(attributes):
            _, call = trace_presentation.call()

        if call.exception is not None:
            raise RuntimeError(f"Weave failed to trace presentation: {call.exception}")

        content_trace_ref = call.ref.uri()
        _debug("presentation Call completed")
    finally:
        _debug("flushing Weave")
        # Flush while the client is still registered. weave.finish() clears the
        # global client first, which breaks pending Content serialization tasks.
        client.finish()
        weave.finish()
        _debug("Weave flushed")

    result = {
        "callId": call.id,
        "traceId": call.trace_id,
        "contentTraceRef": content_trace_ref,
        "contentTraceUrl": call.ui_url,
        "fileSizeBytes": len(pptx_bytes),
    }
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    os.environ.setdefault("WEAVE_PRINT_CALL_LINK", "false")
    main()
