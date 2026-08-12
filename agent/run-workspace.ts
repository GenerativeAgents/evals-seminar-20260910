import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { isVariant, type Variant } from "../variants";

const ROOT = process.cwd();
const SAFE_RUN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export interface RunWorkspaceOptions {
  rootDir?: string;
  now?: Date;
}

function utcTimestamp(date: Date): string {
  return date.toISOString().replace(/[-:T]/g, "").slice(0, 14);
}

export function createRunWorkspace(
  variant: Variant,
  runId: string = crypto.randomUUID(),
  options: RunWorkspaceOptions = {},
): string {
  if (!isVariant(variant)) {
    throw new Error(`Unknown variant: ${variant}`);
  }
  if (!SAFE_RUN_ID.test(runId)) {
    throw new Error(`Unsafe run ID: ${runId}`);
  }

  const rootDir = options.rootDir ?? ROOT;
  const templateDir = path.join(rootDir, "workspaces", variant);
  const agentsFile = path.join(templateDir, "AGENTS.md");
  const skillsDir = path.join(templateDir, ".agent", "skills");
  if (!fs.statSync(agentsFile, { throwIfNoEntry: false })?.isFile()) {
    throw new Error(`Workspace template is missing AGENTS.md: ${agentsFile}`);
  }
  if (!fs.statSync(skillsDir, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error(`Workspace template is missing skills: ${skillsDir}`);
  }

  const parentDir = path.join(rootDir, "tmp", "workspaces");
  fs.mkdirSync(parentDir, { recursive: true });
  const timestamp = utcTimestamp(options.now ?? new Date());
  const workspaceDir = path.join(parentDir, `${timestamp}-${variant}-${runId}`);
  fs.mkdirSync(workspaceDir, { recursive: false });

  fs.copyFileSync(agentsFile, path.join(workspaceDir, "AGENTS.md"));
  fs.cpSync(skillsDir, path.join(workspaceDir, ".agent", "skills"), {
    recursive: true,
  });
  fs.mkdirSync(path.join(workspaceDir, "slides"));
  fs.mkdirSync(path.join(workspaceDir, "large_tool_results"));
  return workspaceDir;
}
