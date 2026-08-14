# Weaveライブ評価 段階的実装計画

## 目的

[evaluation-implementation-plan.md](evaluation-implementation-plan.md)の実装を、一度にすべて行うのではなく、独立して完結・検証できるフェーズへ分割する。各フェーズは次を満たす。

- そのフェーズ単独でコミットできる。
- 完了時点で既存の機能（`npm run agent`、Web UI、現行のDeepEval評価）を壊さない。
- フェーズごとに検証コマンドと完了条件を持つ。

実装内容の詳細仕様は[evaluation-implementation-plan.md](evaluation-implementation-plan.md)を正とし、本書はその実装順序と区切りだけを定義する。

## 決定事項

実装方針ドキュメントに対して、次を確定させた。

- Judge modelは`openrouter/openai/gpt-5.4`とする（現行DeepEval judge `openai/gpt-5.4`のlitellm表記）。
- `source_text`はリポジトリへコミットせず、`publish_dataset.py`の実行時にar5ivから取得してDatasetへ含める。取得済み本文はDataset versionに保存されるため、過去評価の再現性はDataset側で担保される。参加者間のdigest一致は講師のdigest照合で確認する。
- ファイル構成は実装方針ドキュメントの「変更対象」に列挙された最小セットに限定する。設定用・ツール用の追加モジュールは作らない（定数と環境変数検証は`eval/dataset.py`と`eval/scorers.py`に置く）。
- Pythonの単体テストは`eval/tests/test_eval.py`の1ファイルに集約する。TypeScript専用のテスト基盤は追加せず、スモーク実行で確認する。
- 最終検証は3 variantすべてをライブ実行し、Compare evaluationsまで確認する。

## フェーズ一覧

| フェーズ | 内容                                       | 主な成果物                                   | 既存機能への影響 |
| -------- | ------------------------------------------ | -------------------------------------------- | ---------------- |
| 0        | 環境復旧と前提検証                         | 動作するnode_modules                         | なし             |
| 1        | run workspace基盤                          | `agent/run-workspace.ts`、`tmp/`             | なし（追加のみ） |
| 2        | エージェント実行の切り出しとCLI薄化        | `agent-run/runner.ts`、`run.ts`改修          | CLI互換を維持    |
| 3        | Web UIのconversation単位化                 | `app/api/copilotkit/route.ts`改修            | UI挙動改善       |
| 4        | 評価用エントリポイント                     | `agent-run/eval.ts`                          | なし（追加のみ） |
| 5        | Weave依存の追加とDataset publish           | `eval/dataset.py`、`eval/publish_dataset.py` | なし（追加のみ） |
| 6        | scorer実装                                 | `eval/scorers.py`                            | なし（追加のみ） |
| 7        | ライブEvaluationへの移行とDeepEval削除     | `eval/agent_model.py`、`run_eval.py`書き換え | 旧評価を置換     |
| 8        | 3 variantライブ検証                        | Evaluation×3、Compare確認                    | なし             |

フェーズ0〜4はTypeScript側、5〜7はPython側、8は検証である。フェーズ3は他と依存関係がないため、順序を後ろへずらしてもよい。

## Phase 0: 環境復旧と前提検証

現在のcheckoutは部分インストール状態（`node_modules/deepagents`と`langchain-copilotkit`が空、`node_modules/.bin/tsx`欠落）であり、何も動かない。

1. `npm install`を実行する。
2. `npm run check`が通ることを確認する。
3. `node_modules/langchain-copilotkit`の型定義で、`LangChainAgentAdapter`がagentの`streamEvents`/`getState`以外を呼ばないこと、`config.configurable.thread_id`が届くことを確認する（Phase 3の前提）。

**完了条件**: `npm run check`成功。`npm run agent -- 1706.03762 baseline`が現行実装のまま動く。

## Phase 1: run workspace基盤

workspace templateからrun workspaceを作る共通処理を追加する。既存コードはまだ使わないため、影響はない。

- 新規`agent/run-workspace.ts`: `createRunWorkspace(variant, runId)`。
  - variantを`app/variants.ts`の`VARIANTS`で、runIdを`[A-Za-z0-9-]+`で検証する。
  - `tmp/workspaces/<yyyyMMddHHmmss(UTC)>-<variant>-<runId>/`を作成する。
  - templateから`AGENTS.md`と`.agent/skills/`だけをコピーし、空の`slides/`と`large_tool_results/`を作る。
  - 絶対パスを返す。
- `tmp/.gitkeep`を作成し、`.gitignore`へ`tmp/*`と`!tmp/.gitkeep`を追加する。

**完了条件**: `npm run check`成功。一時スクリプトまたはnode REPLからの手動呼び出しで、ディレクトリ名形式・コピー対象の限定（templateの既存`slides/`を引き継がない）を確認。

## Phase 2: エージェント実行の切り出しとCLI薄化

`agent-run/run.ts`を「実行本体」と「CLI」に分離し、CLIをrun workspaceへ移行する。`npm run agent -- <id> <variant>`の互換と`results/<variant>/<id>.json`の既存キーは維持する。

- 新規`agent-run/runner.ts`: `runSlideAgent({arxivId, paperUrl, variant, workspaceDir})`。
  - 現`run.ts`の2ターンロジックを移植する。ターン1のURLは`paperUrl`引数を使う。
  - `on_tool_end`で`generate_pptx`のツール結果（検証済み`slides`を含むJSON）を捕捉し、`success: true`のものを最後勝ちで保持する。ディスクの`slides/<id>.json`再読込は廃止する。
  - `eval/cases.py`の`slides_to_text`をTSへ移植し（`type === "title"`スキップ、スキップ分も進む1始まりindex）、`slide_text`の導出をTS側で一元化する。
  - `agent/weave-agent-tracing.ts`へ`buildConversationId(variant, threadId)`をexportとして追加し、既存の生成箇所（341行目）とrunner.tsの両方から使う。
  - 返り値: `{generationSuccess, slides, slideText, toolCalls, finalText, durationMs, conversationId, failureReason?}`と`backend`（呼び出し元がfinallyでclose）。
  - `generate_pptx`の成功結果が無い場合は例外にせず、`generationSuccess: false`と`failureReason`を返す。
- `agent-run/run.ts`を薄いラッパーへ: 引数パース → `createRunWorkspace(variant, randomUUID())` → `runSlideAgent()` → 従来どおり`results/`へ保存。

**完了条件**: `npm run check`成功。`npm run agent -- 1706.03762 baseline`で`results/baseline/1706.03762.json`が更新され、`tmp/workspaces/`配下に実行ディレクトリができ、`git status`で`workspaces/`が無変更。

## Phase 3: Web UIのconversation単位化

`app/api/copilotkit/route.ts`を、variantごとの固定agentからconversation単位の管理へ変更する。

- variantごとに、`config.configurable.thread_id`をキーとするディスパッチproxy（`streamEvents`/`getState`の2メソッド）を`LangChainAgentAdapter`へ渡す。
- 実agent・backend・run workspaceは`Map<threadId, Promise<...>>`で遅延生成し、同一conversationの複数ターンで再利用する。run workspaceは`createRunWorkspace(variant, sanitize(threadId))`で作る。
- thread_id欠落時はリクエスト単位へフォールバックし、`getState`で未知のthreadには空stateを返す。
- backendはcloseしない（現行同様）。Mapの無制限成長はdevサーバー用途の既知の制約としてコメントで明記する。

**完了条件**: `npm run dev`で1 variantにつき2会話を実施し、`tmp/workspaces/`に会話ごとの別ディレクトリができること、同一会話の2ターン目が同じworkspaceを再利用することを確認。

## Phase 4: 評価用エントリポイント

Pythonから起動されるTypeScript側の入口を追加する。既存フローへの影響はない。

- 新規`agent-run/eval.ts`: `tsx agent-run/eval.ts --arxiv-id <id> --paper-url <url> --variant <variant> --run-id <hex>`。
  - `.env`を自前ロードし、`createRunWorkspace()` → `runSlideAgent()` → run workspace直下へ`evaluation-result.json`を書く。
  - JSONキーはsnake_case: `generation_success, slides, slide_text, tool_calls, final_text, duration_ms, conversation_id`（失敗時は`failure_reason`を追加）。
  - exit 0は「`evaluation-result.json`を書けた」ことを意味する（エージェントの振る舞いとしての失敗でもexit 0）。exit非0またはファイル欠落はインフラエラー。stdout/stderrはログ専用。

**完了条件**: 手動実行1回で`tmp/workspaces/*-baseline-<runid>/evaluation-result.json`のキーと内容（`slide_text`が非空）を確認。

## Phase 5: Weave依存の追加とDataset publish

Python側にWeaveを導入し、Datasetをpublishできるようにする。**この時点ではDeepEvalを削除しない**（旧`run_eval.py`が動く状態を保つ）。

- `pyproject.toml`へ`weave[scorers]==0.53.4`（プリセットscorerのシグネチャを検証済みのバージョンに固定）と`litellm`を追加し、`uv lock` / `uv sync`。
- 新規`eval/dataset.py`:
  - 定数（`DATASET_NAME = "evals-seminar-20260910"`、`PAPER_IDS`、`VARIANTS`）と、環境変数検証を含む`load_settings(*, require_openrouter)`をここに置く。
  - `fetch_paper_text()`を現`eval/cases.py`から移植し、`build_dataset_rows()`が3論文をライブ取得して`{arxiv_id, paper_url, source_text, expected_tools}`を返す。
- 新規`eval/publish_dataset.py`: `weave.init` → `weave.publish(weave.Dataset(...))` → `ref.uri()`とdigestを表示。

**完了条件**: `uv run eval/publish_dataset.py`を2回実行して同一digestになり、Weave UIでversionが1つだけであることを確認。digestを講師照合値として記録。旧`uv run eval/run_eval.py baseline`が引き続き動く。

## Phase 6: scorer実装

- 新規`eval/scorers.py`: `JUDGE_MODEL = "openrouter/openai/gpt-5.4"`と4つのscorer、`build_scorers()`。
  - `tool_correctness`（function-based）、プリセット`SummarizationScorer`/`HallucinationFreeScorer`への移譲wrapper、`SlideQualityScorer`（class-based）。実装は方針ドキュメントのスニペットに従う。
  - `output`の`slide_text`が無い場合はjudgeを呼ばず、`failure_reason`を含む理由付きfailを返す。judge APIエラーはcatchせず伝播させる（0点にしない）。

**完了条件**: importと`build_scorers()`の構築が通る。judgeの実疎通はPhase 8冒頭で確認する。

## Phase 7: ライブEvaluationへの移行とDeepEval削除

評価の本体を置き換える。このフェーズで初めて旧評価フローが消える。

- 新規`eval/agent_model.py`: `SlideAgentModel(weave.Model)`（フィールドは`variant`のみ）と`run_agent_process()`。
  - `run_id = uuid4().hex`を生成し、`asyncio.create_subprocess_exec`で`node_modules/.bin/tsx agent-run/eval.ts --arxiv-id ... --run-id ...`を起動する（タイムアウト900秒、超過はkill+例外）。
  - 終了後に`tmp/workspaces/*-<variant>-<run_id>`をglobして`evaluation-result.json`を読む。glob 0件/複数件・exit非0・必須キー欠落は例外にし、Weave上で行エラーとする。
- `eval/run_eval.py`を書き換え: positional `variant`のみのCLI。publish済みDatasetを`weave.ref(f"weave:///{WEAVE_PROJECT}/object/{DATASET_NAME}:latest").get()`で取得し（未publish時は`publish_dataset.py`の実行を案内）、`weave.Evaluation`を実行して集計・Dataset ref・variantを表示する。
- 削除: `eval/cases.py`、`eval/metrics.py`、`pyproject.toml`の`deepeval`、残骸の`eval/__pycache__`・`eval/tests/__pycache__`。`uv lock` / `uv sync`。
- 新規`eval/tests/test_eval.py`（1ファイルに集約）:
  - `tool_correctness`のpassed/missing_tools。
  - 移譲wrapper 2種の引数対応付け（fake差し替え）と、`slide_text`欠落時にjudgeが呼ばれないこと。
  - `SlideQualityScorer`のscore/reason抽出と、judge例外が伝播すること。
  - `run_agent_process`のsubprocess fake化テスト（正常読取・exit非0・glob 0件/複数件・必須キー欠落の例外）。
  - `build_dataset_rows`の行の形（fetchはfake）。
  - pytestは`[dependency-groups] dev`へ追加し、`[tool.pytest.ini_options]`で`testpaths = ["eval/tests"]`、`asyncio_mode = "auto"`を設定する。

**完了条件**: `uv run pytest`全緑。`npm run check`成功。`grep -r deepeval pyproject.toml eval/`が0件。

## Phase 8: 3 variantライブ検証

まずjudge疎通（litellmで`openrouter/openai/gpt-5.4`を1回呼び、JSON出力を確認）を行い、その後3 variantを実行する。

```bash
uv run eval/run_eval.py baseline
uv run eval/run_eval.py improvement-1
uv run eval/run_eval.py improvement-2
```

確認項目:

- 各Evaluationに4 scorer列と理由が記録され、行エラーが無い。
- 3つのEvaluationが同一のDataset ref（digest）とscorer versionを参照している。
- Compare evaluationsでvariant間比較が表示できる。
- Evaluation行の`conversation_id`からAgents画面のTraceを特定できる。
- 1 variantあたりの所要時間を実測し、ハンズオンのタイムボックスと照合する。

**完了条件**: 上記5点。方針ドキュメントの手順12（ローカルJSONエクスポート）は必要になるまで実装しない。README更新は方針ドキュメントどおり後続作業とする。

## 進捗チェックリスト

- [x] Phase 0: 環境復旧と前提検証
- [x] Phase 1: run workspace基盤
- [x] Phase 2: エージェント実行の切り出しとCLI薄化
- [x] Phase 3: Web UIのconversation単位化(dev UIでの手動確認は未実施)
- [x] Phase 4: 評価用エントリポイント
- [x] Phase 5: Weave依存の追加とDataset publish(digest: IRXwDhPUgvynwdetJlkd6Q4tyDKSX16XPta0JUmtNZ8)
- [x] Phase 6: scorer実装
- [x] Phase 7: ライブEvaluationへの移行とDeepEval削除
- [x] Phase 8: 3 variantライブ検証(3論文×3 variant、行エラーなし。Compare画面の目視確認は未実施)
