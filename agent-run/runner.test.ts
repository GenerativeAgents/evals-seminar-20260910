import assert from "node:assert/strict";
import test from "node:test";
import { slidesToText } from "./runner";

test("slidesToText excludes title slides and keeps evaluation content", () => {
  const text = slidesToText({
    title: "Paper title",
    slides: [
      { type: "title", title: "Paper title", subtitle: "Author" },
      { type: "content", title: "Main claim", bullets: ["Evidence"] },
    ],
  });
  assert.equal(
    text,
    "# Paper title\n## Slide 2 [content] Main claim\n- Evidence",
  );
});
