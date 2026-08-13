# AI Agent評価体系構築セミナー ハンズオン（2026/9/10）

「arXiv論文→スライド生成」ワークフローを題材に、W&B Weaveのライブ評価で「計測→改善→再計測」のループを回すサンプルです。

Software Design誌「実践LLMアプリケーション開発」の[第32回](https://github.com/mahm/softwaredesign-llm-application/tree/main/32)（エージェント本体と対話UI）および[第36回](https://github.com/mahm/softwaredesign-llm-application/tree/main/36)（評価）のサンプルコードをセミナー用に再構成しています。

> [!NOTE]
> 元のサンプルコードはNode.jsとBunを使用していますが、本リポジトリは受講者がインストールするツールを減らすため、Node.jsのみで動くように変更しています。

エージェント本体はTypeScript（deepagents）、評価はPython（W&B Weave）で実装しています。

3つのスキルの作り込み段階を持ち、各改善を直前の段階における改善と比較します。

```
baseline        スキルはワークフローの機構のみ(取得手順・JSON形式・枚数・確認フロー)
  ↓ +スライド設計ガイド                → 主にSlide Qualityで比較
improvement-1   詰め込み禁止・主張型タイトル・論理的な流れ
  ↓ +保存前の事実確認                  → 主にHallucination Free(本文への忠実性)で比較
improvement-2   本文照合・一般化禁止・照合できない数値は書かない
```

## 前提条件

以下を準備してください。

- OpenRouter APIキー（エージェント実行: `deepseek/deepseek-v4-flash`、評価judge: `openai/gpt-5.4`）
- W&B APIキーと自分のW&B entity（Weaveを主題とするため、本ハンズオンでは必須です）
- [Visual Studio Code](docs/setup/install-vscode.md)
- Node.jsと`uv`が使える実行環境（次の方法A・方法Bのどちらかでインストール）

### 方法A: Dev Containerを使う

Visual Studio CodeのDev Containers拡張機能を使い、コンテナ内に開発環境を構築します。コンテナの起動時にNode.jsと`uv`が自動でインストールされます。

1. [Dockerをインストールする](docs/setup/install-docker.md)
2. [Dev Containers拡張機能をインストールする](docs/setup/install-devcontainer.md)

### 方法B: Node.jsとuvを直接インストールする

1. [Node.js](https://nodejs.org/)（v22以上）をインストールします
2. [uv](https://docs.astral.sh/uv/getting-started/installation/)をインストールします

## セットアップ

1. リポジトリをクローンします

```bash
git clone https://github.com/GenerativeAgents/evals-seminar-20260910.git
```

2. Visual Studio Codeでリポジトリを開きます

```bash
cd evals-seminar-20260910
code .
```

> [!NOTE]
> Dev Containerを使用する場合、Visual Studio Codeがリポジトリ内の`.devcontainer/devcontainer.json`を検出し、「Reopen in Container」（コンテナで再度開く）という通知が表示されます。
> 通知をクリックするか、コマンドパレットから「Dev Containers: Reopen in Container」を実行します。
> 初回起動時はコンテナのビルドに数分かかります。完了すると、コンテナ内の開発環境でVisual Studio Codeが開きます。
> Dev Containerを使用する場合、以降のコマンドは、コンテナ内のターミナルで実行してください。

3. 依存関係をインストールします。

```bash
npm install
uv sync
```

4. `.env` を作成し、APIキーを設定します。

```bash
cp .env.sample .env
```

作成した `.env` を開き、各値を自分のキーに書き換えます。

```env
OPENROUTER_API_KEY=your_openrouter_api_key_here
WANDB_API_KEY=your_wandb_api_key_here
WEAVE_PROJECT=evals-seminar-20260910
```

- `OPENROUTER_API_KEY`: エージェント実行と評価judgeの両方で使用（OpenRouter経由でdeepseek-v4-flashとopenai/gpt-5.4を呼び出す）
- `WANDB_API_KEY`: WeaveへのAgent Trace送信と評価の記録に使用
- `WEAVE_PROJECT`: Dataset・Evaluation・Traceの記録先。project名のみの場合は自分のdefault entityに記録されます。`<your-entity>/evals-seminar-20260910` のようにentityを明示することもできます

## ワークスペースの構成

`workspaces/<variant>/` は設定（`AGENTS.md`）とスキル（`.agent/skills/`）を保持する読み取り専用のworkspace templateです。CLI・Web UI・評価はいずれもtemplateを直接使わず、実行時に `tmp/workspaces/<yyyyMMddHHmmss>-<variant>-<runId>/` へrun workspaceを作成して、その中で動作します。

| 実行経路         | run workspaceの分離単位 |
| ---------------- | ----------------------- |
| 手動CLI          | コマンド実行ごと        |
| Web UI           | conversationごと        |
| Weave Evaluation | Dataset行の実行ごと     |

これにより、並列実行や再実行でファイルが競合したり、過去の生成物を誤って読むことがありません。run workspaceは実行後も調査用に残り、自動削除されません（`tmp/`配下はGit管理外です。不要になったら手動で削除してください）。

## UIの起動（対話アプリ）

Next.js + CopilotKitで実装された対話UIを起動します。エージェントの挙動をブラウザ上で対話的に確認できます。

```bash
npm run dev
```

http://localhost:3000 を開き、チャット欄にarXiv論文のURLを貼り付けるとスライド生成が始まります。左側にスライドのプレビューが表示され、生成完了後はPPTXをダウンロードできます。

ヘッダーの「ワークスペース」で `baseline` / `improvement-1` / `improvement-2` を切り替えられ、スキルの作り込み段階による挙動の違いを対話で見比べられます。切り替えると会話はリセットされます。

会話ごとに独立したrun workspaceが作られ、同じ会話の複数ターンでは同じrun workspaceを再利用します。

> [!NOTE]
> 会話の状態はインメモリで保持されるため、devサーバを再起動すると過去の会話の続きからは再開できません。

各ユーザー発言は1 Turnとして、モデル呼び出し・ツール呼び出し・SubAgent呼び出しがWeaveのAgents画面に記録されます。SubAgentは呼び出し全体のみを記録し、その内部のモデル・ツール呼び出しは記録しません。

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

`results/` は手動実行の成果物置き場であり、後述のライブ評価はこのファイルを読みません。

ヘッドレスランナーの2ターンも同じconversation IDでWeaveへ送信されます。プロセス終了前にOpenTelemetry spanをflushするため、短命なCLI実行でもトレースが欠落しないようにしています。

### 使用論文

- Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser, Illia Polosukhin. "Attention Is All You Need." NeurIPS 2017. [arXiv:1706.03762](https://arxiv.org/abs/1706.03762)
- Jeremy Yang, Noah Yonack, Kate Zyskowski, Denis Yarats, Johnny Ho, Jerry Ma. "The Adoption and Usage of AI Agents: Early Evidence from Perplexity." 2025. [arXiv:2512.07828](https://arxiv.org/abs/2512.07828)
- Shirley Wu, Evelyn Choi, Arpandeep Khatua, Zhanghan Wang, Joy He-Yueya, Tharindu Cyril Weerasooriya, Wei Wei, Diyi Yang, Jure Leskovec, James Zou. "HumanLM: Simulating Users with State Alignment Beats Response Imitation." 2026. [arXiv:2603.03303](https://arxiv.org/abs/2603.03303)

## 評価の実行（Weaveライブ評価）

評価はWeaveの`Evaluation`によるライブ評価です。Datasetの各行についてTypeScriptエージェントをその場で実行し、出力をscorerで採点します。評価結果の正本はWeaveであり、ローカルJSONが必要な場合はEvaluation完了後にWeave Evaluation APIからエクスポートします（評価入力としては使用しません）。

### 1. Datasetのpublish（初回のみ）

3本の論文（上記「使用論文」）を、自分のprojectへWeave Datasetとしてpublishします。

```bash
uv run eval/publish_dataset.py
```

行データはリポジトリで固定されており、`source_text`（judgeが参照する評価基準の論文本文）はpublish時にar5ivから取得してDataset versionへ保存されます。Weaveのオブジェクトはcontent-addressedのため、同じ内容の再publishは新しいversionを作らず、Datasetのversion（digest）は参加者全員で一致します。

### 2. ライブ評価

```bash
uv run eval/run_eval.py baseline
```

publish済みのDatasetをrefで取得し、3論文それぞれについてエージェントを実行して採点します。各Dataset行の実行は1回で、反復回数のオプションはありません。

品質軸は次の4つです。

| 品質軸             | scorer                | 実装                                     |
| ------------------ | --------------------- | ---------------------------------------- |
| Tool Correctness   | `tool_correctness`    | 自作function-based scorer（決定的判定）  |
| Summarization      | `summarization`       | プリセット`SummarizationScorer`への移譲  |
| Hallucination Free | `hallucination_free`  | プリセット`HallucinationFreeScorer`への移譲 |
| Slide Quality      | `SlideQualityScorer`  | 自作class-based scorer（LLM-as-a-judge） |

scorerは数値だけでなく判定理由も返し、Weave上でDataset・Model・scorerのバージョンとともに記録されます。judgeはlitellm経由の`openrouter/openai/gpt-5.4`です。

### 3. variantの比較（Compare evaluations）

時間と予算に余裕がある場合は、残りのvariantも同じコマンドで評価します。

```bash
uv run eval/run_eval.py improvement-1
uv run eval/run_eval.py improvement-2
```

同じDataset version refと同じscorerバージョンで実行されるため、WeaveのEvals画面で複数のEvaluationを選択してCompareを開くと、variant間で品質軸ごとの変化を行単位まで比較できます。

### 4. Evaluation行からAgent Traceを調査する

Model出力の`conversation_id`（`<variant>:<thread_id>`形式）は、Agents画面のconversation IDと対応しています。スコアが低い行や回帰した行を見つけたら、`conversation_id`でAgents画面のTraceを開き、モデル呼び出し・ツール呼び出し・SubAgent呼び出しから原因を調査してください。

## ファイル構成

```text
.
├── agent/                      # 第32回から流用したエージェント本体
│   ├── agent.ts                # createDeepAgent定義(モデルはOpenRouter経由に変更)
│   ├── generate-pptx-tool.ts   # generate_pptxツール(スキーマ検証内蔵)
│   ├── run-workspace.ts        # templateからrun workspaceを作成する共通処理
│   ├── system-prompt.ts        # システムプロンプト
│   ├── weave-agent-tracing.ts  # Agent Trace用ミドルウェアとラッパー
│   └── weave-client.ts         # Weave初期化・flush
├── agent-run/
│   ├── cli.ts                  # 手動実行用CLI(薄いラッパー)
│   ├── runner.ts               # runSlideAgent()本体(2ターン実行と結果の捕捉)
│   └── eval.ts                 # 評価用エントリポイント(evaluation-result.jsonを出力)
├── app/                        # 第32回から移植した対話UI(Next.js + CopilotKit)
│   ├── api/copilotkit/route.ts # CopilotKitランタイム(conversation単位のagent管理)
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
│   ├── run_eval.py             # weave.Evaluationの実行
│   └── tests/test_eval.py      # 単体テスト
├── results/
│   ├── baseline/               # 手動実行(npm run agent)の成果物
│   ├── improvement-1/
│   └── improvement-2/
├── docs/
│   ├── setup/                  # ツールのインストール手順
│   └── logs/                   # 取り組みごとの実装方針・計画の記録
├── package.json
├── pyproject.toml
└── .env.sample
```

## 確認コマンド

```bash
npm run check
uv run pytest
```

## 参考リンク

- [Weave Evaluations overview](https://docs.wandb.ai/weave/guides/core-types/evaluations)
- [Weave Scoring overview](https://docs.wandb.ai/weave/guides/evaluation/scorers)
- [Weave Predefined scorers](https://docs.wandb.ai/weave/guides/evaluation/builtin_scorers)
- [Weave Datasets](https://docs.wandb.ai/weave/guides/core-types/datasets)
- [Weave Compare evaluations](https://docs.wandb.ai/weave/guides/evaluation/compare_evals)
- [W&B Weave Agent Trace](https://docs.wandb.ai/weave/guides/tracking/trace-agents)
- [LangChain JS DeepAgents docs](https://docs.langchain.com/oss/javascript/deepagents/overview)
- [OpenRouter DeepSeek V4 Flash](https://openrouter.ai/deepseek/deepseek-v4-flash)
- [ar5iv](https://ar5iv.labs.arxiv.org/)
