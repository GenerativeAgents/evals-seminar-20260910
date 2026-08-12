import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createRunWorkspace } from "./run-workspace";

function fixtureRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "slide-workspace-test-"));
  const template = path.join(root, "workspaces", "baseline");
  fs.mkdirSync(path.join(template, ".agent", "skills", "sample"), {
    recursive: true,
  });
  fs.writeFileSync(path.join(template, "AGENTS.md"), "instructions\n");
  fs.writeFileSync(
    path.join(template, ".agent", "skills", "sample", "SKILL.md"),
    "skill\n",
  );
  fs.mkdirSync(path.join(template, "slides"));
  fs.writeFileSync(path.join(template, "slides", "stale.json"), "{}\n");
  fs.mkdirSync(path.join(template, "large_tool_results"));
  fs.writeFileSync(path.join(template, "large_tool_results", "stale"), "old\n");
  return root;
}

test("creates isolated run workspaces without copying generated files", () => {
  const root = fixtureRoot();
  try {
    const now = new Date("2026-08-12T15:30:45Z");
    const first = createRunWorkspace("baseline", "run-a", { rootDir: root, now });
    const second = createRunWorkspace("baseline", "run-b", { rootDir: root, now });
    assert.notEqual(first, second);
    assert.match(path.basename(first), /^20260812153045-baseline-run-a$/);
    assert.equal(fs.readFileSync(path.join(first, "AGENTS.md"), "utf8"), "instructions\n");
    assert.equal(
      fs.readFileSync(
        path.join(first, ".agent", "skills", "sample", "SKILL.md"),
        "utf8",
      ),
      "skill\n",
    );
    assert.deepEqual(fs.readdirSync(path.join(first, "slides")), []);
    assert.deepEqual(fs.readdirSync(path.join(first, "large_tool_results")), []);
    assert.ok(
      fs.existsSync(
        path.join(root, "workspaces", "baseline", "slides", "stale.json"),
      ),
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("rejects unsafe run IDs before constructing a path", () => {
  const root = fixtureRoot();
  try {
    assert.throws(
      () => createRunWorkspace("baseline", "../escape", { rootDir: root }),
      /Unsafe run ID/,
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
