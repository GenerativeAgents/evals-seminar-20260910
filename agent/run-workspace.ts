import fs from "node:fs";
import path from "node:path";
import { VARIANTS, type Variant } from "../app/variants";

// Next.js(Turbopack)にもバンドルされるため、import.meta.urlではなくcwdを使う。
// CLI(npm run agent)、評価(agent-run/eval.ts)、dev serverはいずれも
// リポジトリ直下をcwdとして実行される。
const ROOT = process.cwd();
const RUN_ID_PATTERN = /^[A-Za-z0-9-]+$/;

function utcTimestamp(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getUTCFullYear()}` +
    pad(date.getUTCMonth() + 1) +
    pad(date.getUTCDate()) +
    pad(date.getUTCHours()) +
    pad(date.getUTCMinutes()) +
    pad(date.getUTCSeconds())
  );
}

/**
 * workspace template(workspaces/<variant>/)から独立したrun workspaceを
 * tmp/workspaces/<yyyyMMddHHmmss>-<variant>-<runId>/ に作成する。
 * templateは読み取り専用として扱い、AGENTS.md と .agent/skills/ だけをコピーする。
 */
export function createRunWorkspace(variant: string, runId: string): string {
  if (!(VARIANTS as readonly string[]).includes(variant)) {
    throw new Error(
      `Unknown variant: ${variant} (expected one of ${VARIANTS.join(", ")})`,
    );
  }
  if (!RUN_ID_PATTERN.test(runId)) {
    throw new Error(
      `Invalid run ID: ${runId} (expected characters [A-Za-z0-9-])`,
    );
  }

  const templateDir = path.join(ROOT, "workspaces", variant);
  const templateAgentsMd = path.join(templateDir, "AGENTS.md");
  const templateSkillsDir = path.join(templateDir, ".agent", "skills");
  if (!fs.existsSync(templateAgentsMd)) {
    throw new Error(`Workspace template is missing: ${templateAgentsMd}`);
  }
  if (!fs.existsSync(templateSkillsDir)) {
    throw new Error(`Workspace template is missing: ${templateSkillsDir}`);
  }

  const parentDir = path.join(ROOT, "tmp", "workspaces");
  fs.mkdirSync(parentDir, { recursive: true });
  const workspaceDir = path.join(
    parentDir,
    `${utcTimestamp(new Date())}-${variant}-${runId}`,
  );
  // 同名ディレクトリ(同秒・同variant・同runId)の衝突は上書きせずエラーにする
  fs.mkdirSync(workspaceDir);

  fs.copyFileSync(templateAgentsMd, path.join(workspaceDir, "AGENTS.md"));
  fs.cpSync(templateSkillsDir, path.join(workspaceDir, ".agent", "skills"), {
    recursive: true,
  });
  fs.mkdirSync(path.join(workspaceDir, "slides"));
  fs.mkdirSync(path.join(workspaceDir, "large_tool_results"));

  return workspaceDir;
}

export type { Variant };
