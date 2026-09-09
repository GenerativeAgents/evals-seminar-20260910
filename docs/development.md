# 開発者向けガイド

このリポジトリの内部構成と、ハンズオンの手順（[README](../README.md)）には載せていない実行経路・生成物の扱いをまとめます。コードを変更する人向けです。

## ファイル構成

```text
.
├── agent/                      # 第32回から流用したエージェント本体
│   ├── agent.ts                # createDeepAgent定義(モデルはOpenRouter経由に変更)
│   ├── generate-pptx-tool.ts   # generate_pptxツール(スキーマ検証内蔵)
│   ├── run-workspace.ts        # templateからrun workspaceを作成する共通処理
│   ├── system-prompt.ts        # システムプロンプト
│   ├── weave-agent-tracing.ts  # Agent Trace用ミドルウェアとラッパー
│   ├── weave-client.ts         # Weave初期化・flush
│   ├── presentation-content-tracer.ts # 生成したPPTXをWeaveへ記録する
│   └── trace-presentation.py   # PPTXをHTMLプレビュー付きでWeaveへ記録する
├── agent-run/
│   ├── cli.ts                  # 手動実行用CLI(薄いラッパー)
│   ├── runner.ts               # runSlideAgent()本体(2ターン実行と結果の捕捉)
│   └── eval.ts                 # 評価用エントリポイント(evaluation-result.jsonを出力)
├── app/                        # 第32回から移植した対話UI(Next.js + CopilotKit)
│   ├── api/copilotkit/route.ts # CopilotKitランタイム(conversation単位のagent管理)
│   ├── api/pptx-download/      # PPTXダウンロード(ダウンロード操作をWeaveへ記録)
│   ├── api/presentation-trace/ # 生成したPPTXをWeaveへ記録するAPI
│   ├── components/             # スライドプレビュー・ツール呼び出し表示
│   ├── page.tsx                # 画面本体(ワークスペース切り替え付き)
│   └── variants.ts             # ワークスペース一覧の共有定数
├── workspaces/                 # 読み取り専用のworkspace template
│   ├── baseline/               # 機構のみのスキル
│   ├── improvement-1/          # +スライド設計ガイド
│   └── improvement-2/          # +保存前の事実確認
├── tmp/
│   └── workspaces/             # 実行時に作られるrun workspace(Git管理外)
├── eval/
│   ├── dataset.py              # Dataset行の組み立てと環境変数検証
│   ├── publish_dataset.py      # 自分のprojectへDatasetをpublish
│   ├── scorers.py              # 4つの品質軸のscorer
│   ├── agent_model.py          # SlideAgentModelとsubprocess境界
│   ├── run_eval.py             # EvaluationLoggerによるライブ評価の実行
│   └── tests/test_eval.py      # 単体テスト
├── results/                    # 手動実行(npm run agent)の成果物(Git管理外・実行時に自動生成)
├── docs/
│   ├── setup/                  # ツールのインストール手順
│   ├── development.md          # このファイル
│   └── logs/                   # 取り組みごとの実装方針・計画の記録(元リポジトリの評価実験アーカイブを含む)
├── .agents/skills/             # W&B Skills(wandb/skillsから導入。skills-lock.jsonで管理)
├── package.json
├── pyproject.toml
└── .env.sample
```

## 確認コマンド

```bash
npm run check
uv run pytest
```

`npm run check`はTypeScriptの型チェック、`uv run pytest`はPython側の単体テストです。単体テストはjudge LLMを呼ばず、litellm・プリセットscorer・subprocessをfakeへ差し替えています。

## ワークスペースの構成

`workspaces/<variant>/` は設定（`AGENTS.md`）とスキル（`.agent/skills/`）を保持する読み取り専用のworkspace templateです。CLI・Web UI・評価はいずれもtemplateを直接使わず、実行時に `tmp/workspaces/<yyyyMMddHHmmss>-<variant>-<runId>/` へrun workspaceを作成して、その中で動作します。

| 実行経路         | run workspaceの分離単位 |
| ---------------- | ----------------------- |
| 手動CLI          | コマンド実行ごと        |
| Web UI           | conversationごと        |
| Weave Evaluation | Dataset行の実行ごと     |

これにより、並列実行や再実行でファイルが競合したり、過去の生成物を誤って読むことがありません。run workspaceは実行後も調査用に残り、自動削除されません（`tmp/`配下はGit管理外です。不要になったら手動で削除してください）。

templateからコピーするのは`AGENTS.md`と`.agent/skills/`だけで、`slides/`と`large_tool_results/`は空で作られます。

## Web UI

Next.js + CopilotKitで実装された対話UIです。会話ごとに独立したrun workspaceが作られ、同じ会話の複数ターンでは同じrun workspaceを再利用します。ヘッダーの「ワークスペース」を切り替えると会話はリセットされます。

会話の状態はインメモリで保持されるため、devサーバを再起動すると過去の会話の続きからは再開できません。

`WANDB_API_KEY`、`WANDB_ENTITY`、`WANDB_PROJECT`を設定すると、各ユーザー発言を1 Turnとして、モデル呼び出し・ツール呼び出し・SubAgent呼び出しが`${WANDB_ENTITY}/${WANDB_PROJECT}`のWeave Agents画面に記録されます。SubAgentは呼び出し全体のみを記録し、その内部のモデル・ツール呼び出しは記録しません。

UIでスライドが生成されると、PPTX本体もWeaveの`trace_presentation` Callへ自動的に記録されます。CallにはWeave上で確認できるHTMLプレビューも付き、対応するAgent TraceにはCall参照が`trace_presentation`ツール結果として残ります。同じconversation内の同一スライドは1回だけ記録されるため、UIの再描画でCallが重複することはありません。PPTXのダウンロード操作は同じconversation IDへ別イベントとして記録されます。

## エージェントの実行（ヘッドレスランナー）

ヘッドレスランナーで同じワークフローをコマンドラインから再現します。

一度のコマンド実行で、以下の2つの会話ターンが自動で実行されます。

1. ターン1: 論文URLを渡す → エージェントが論文を取得・分析し、アウトラインを提案する
2. ターン2: 「OKです。この構成でスライドを生成してください。」→ `generate_pptx` ツールで生成する

```bash
npm run agent -- 1706.03762 baseline
npm run agent -- 1706.03762 improvement-1
npm run agent -- 1706.03762 improvement-2
```

実行結果は `results/<variant>/<arXiv ID>.json` に保存されます。スライドJSON・実行中のツール呼び出し（サブエージェント内を含む）・所要時間が入っています。

`results/` は手動実行の成果物置き場（Git管理外・実行時に自動生成）であり、ライブ評価はこのファイルを読みません。元リポジトリでの改善実験の出力は [docs/logs/20260813-archive-original-eval-results/](logs/20260813-archive-original-eval-results/) にアーカイブしています。

ヘッドレスランナーの2ターンも同じconversation IDでWeaveへ送信されます。プロセス終了前にOpenTelemetry spanをflushするため、短命なCLI実行でもトレースが欠落しないようにしています。

## 評価の内部

### ライブ評価

評価はWeaveの`EvaluationLogger`によるライブ評価です。Datasetの各行についてTypeScriptエージェントを別プロセス（`agent-run/eval.ts`）で実行し、run workspaceに書かれた`evaluation-result.json`をModel出力として読み取り、scorerで採点します。各Dataset行の実行は1回で、反復回数のオプションはありません。

評価結果の正本はWeaveであり、ローカルにJSONは残しません。ローカルJSONが必要な場合はEvaluation完了後にWeave Evaluation APIからエクスポートします（評価入力としては使用しません）。

プロセスの起動失敗・タイムアウト（900秒）・JSONプロトコル違反はインフラエラーとして例外にし、Weave上でもその行の実行をerrorにします。judge LLMのAPIエラーは0点へ変換せず、そのままscorer errorとして伝播させます（`litellm.num_retries = 3`で再試行したうえで）。

### Dataset

行データはリポジトリで固定されており、`source_text`（judgeが参照する評価基準の論文本文）はpublish時にar5ivから取得してDataset versionへ保存されます。Weaveのオブジェクトはcontent-addressedのため、同じ内容の再publishは新しいversionを作らず、Datasetのversion（digest）は参加者全員で一致します。

`run_eval.py`は`weave:///<entity>/<project>/object/evals-seminar-20260910:latest`をrefで取得して使い、行データを組み立て直しません。

### Evaluation行とAgent Traceの紐づけ

各Dataset行の実行時に、`EvaluationLogger.log_prediction()`が発行した`weave.eval.run_id`と`weave.eval.predict_and_score_call_id`を`--eval-context`としてTypeScriptエージェントへ渡します。これらの属性はモデル呼び出し・ツール呼び出し・SubAgent呼び出しを含む全Agent spanへ記録されるため、Evaluation詳細の「View spans」から対応するAgent Traceを直接調査できます。

Agent TraceはWeaveのcalls（Traces画面）とは別のagents planeに保存されます。`weave/agents/...`のconversation IDは`get_calls`では引けず、W&B Skillsの`wandb-primary`が持つ`weave_agent_ops.py`のようにagents向けのAPIで読みます。

LLM spanにはOpenRouter応答のinput/output/reasoning/cache token usageを記録し、`gen_ai.usage.total_tokens`とOpenRouterが返す実課金値`openrouter.usage.cost`も保存します。Model出力の`conversation_id`（`<variant>:<thread_id>`形式）はAgents画面のconversation IDにも対応しています。

## 環境変数

`.env`は`eval/dataset.py`（Python）と`agent-run/cli.ts` / `agent-run/eval.ts`（TypeScript）がそれぞれ読みます。記録先は`WANDB_ENTITY`と`WANDB_PROJECT`（省略時は`evals-seminar-20260910`）から`<entity>/<project>`を組み立てます。`WANDB_ENTITY`が未設定か雛形のままなら、Python側はエラーで止まり、TypeScript側はAgent Traceを無効にして警告を出します。
