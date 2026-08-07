# AI Agent評価体系構築セミナー ハンズオン（2026/9/10）

「arXiv論文→スライド生成」ワークフローを題材に、DeepEvalのメトリクスで「計測→改善→再計測」のループを回すサンプルです。

Software Design誌「実践LLMアプリケーション開発」の[第32回](https://github.com/mahm/softwaredesign-llm-application/tree/main/32)（エージェント本体と対話UI）および[第36回](https://github.com/mahm/softwaredesign-llm-application/tree/main/36)（評価）のサンプルコードをセミナー用に再構成しています。

> [!NOTE]
> 元のサンプルコードはNode.jsとBunを使用していますが、本リポジトリは受講者がインストールするツールを減らすため、Node.jsのみで動くように変更しています。

エージェント本体はTypeScript（deepagents）、評価はPython（DeepEval）で実装しています。

3つのスキルの作り込み段階を持ち、各改善を直前の段階における改善と比較します。

```
baseline        スキルはワークフローの機構のみ(取得手順・JSON形式・枚数・確認フロー)
  ↓ +スライド設計ガイド                → G-Eval(見せ方)で比較
improvement-1   詰め込み禁止・主張型タイトル・論理的な流れ
  ↓ +保存前の事実確認                  → Summarization(忠実性×網羅)で比較
improvement-2   本文照合・一般化禁止・照合できない数値は書かない
```

## 前提条件

以下を準備してください。

- OpenRouter APIキー（エージェント実行: `deepseek/deepseek-v4-flash`、評価judge: `openai/gpt-5.4`）
- [Visual Studio Code](docs/install-vscode.md)
- Node.jsと`uv`が使える実行環境（次の方法A・方法Bのどちらかでインストール）

### 方法A: Dev Containerを使う

Visual Studio CodeのDev Containers拡張機能を使い、コンテナ内に開発環境を構築します。コンテナの起動時にNode.jsと`uv`が自動でインストールされます。

1. [Dockerをインストールする](docs/install-docker.md)
2. [Dev Containers拡張機能をインストールする](docs/install-devcontainer.md)

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

作成した `.env` を開き、`your_openrouter_api_key_here` の部分を自分のOpenRouter APIキーに書き換えます。

```env
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

- `OPENROUTER_API_KEY`: エージェント実行と評価judgeの両方で使用（OpenRouter経由でdeepseek-v4-flashとopenai/gpt-5.4を呼び出す）

## UIの起動（対話アプリ）

Next.js + CopilotKitで実装された対話UIを起動します。エージェントの挙動をブラウザ上で対話的に確認できます。

```bash
npm run dev
```

http://localhost:3000 を開き、チャット欄にarXiv論文のURLを貼り付けるとスライド生成が始まります。左側にスライドのプレビューが表示され、生成完了後はPPTXをダウンロードできます。

ヘッダーの「ワークスペース」で `baseline` / `improvement-1` / `improvement-2` を切り替えられ、スキルの作り込み段階による挙動の違いを対話で見比べられます。切り替えると会話はリセットされます。

> [!NOTE]
> 会話の状態はインメモリで保持されるため、devサーバを再起動すると過去の会話の続きからは再開できません。

## エージェントの実行（ヘッドレスランナー）

評価のためにヘッドレスランナーで同じワークフローを再現します。

一度のコマンド実行で、以下の2つの会話ターンが自動で実行されます。

1. ターン1: 論文URLを渡す → エージェントが論文を取得・分析し、アウトラインを提案する
2. ターン2: 「OKです。この構成でスライドを生成してください。」→ `generate_pptx` ツールで生成する

```bash
npm run agent -- 1706.03762 baseline
npm run agent -- 1706.03762 improvement-1
npm run agent -- 1706.03762 improvement-2
```

実行結果は `results/<variant>/<arXiv ID>.json` に保存されます。
スライドJSON・実行中のツール呼び出し（サブエージェント内を含む）・所要時間が入っており、評価はこのファイルだけを読みます。

記事の実験で使ったデータセットは次の3本です。

```bash
for id in 1706.03762 2512.07828 2603.03303; do
  npm run agent -- "$id" baseline
  npm run agent -- "$id" improvement-1
  npm run agent -- "$id" improvement-2
done
```

### 使用論文

- Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser, Illia Polosukhin. "Attention Is All You Need." NeurIPS 2017. [arXiv:1706.03762](https://arxiv.org/abs/1706.03762)
- Jeremy Yang, Noah Yonack, Kate Zyskowski, Denis Yarats, Johnny Ho, Jerry Ma. "The Adoption and Usage of AI Agents: Early Evidence from Perplexity." 2025. [arXiv:2512.07828](https://arxiv.org/abs/2512.07828)
- Shirley Wu, Evelyn Choi, Arpandeep Khatua, Zhanghan Wang, Joy He-Yueya, Tharindu Cyril Weerasooriya, Wei Wei, Diyi Yang, Jure Leskovec, James Zou. "HumanLM: Simulating Users with State Alignment Beats Response Imitation." 2026. [arXiv:2603.03303](https://arxiv.org/abs/2603.03303)

## 評価の実行

```bash
uv run eval/run_eval.py baseline --repeat 3
uv run eval/run_eval.py improvement-1 --repeat 3
uv run eval/run_eval.py improvement-2 --repeat 3
```

スコアと理由は `results/eval/<variant>.json` に保存されます。
`--repeat` はjudgeのブレを見るための繰り返し実行です。単発のスコアではなく複数回の平均で比較してください。

## ファイル構成

```text
.
├── agent/                      # 第32回から流用したエージェント本体
│   ├── agent.ts                # createDeepAgent定義(モデルはOpenRouter経由に変更)
│   ├── generate-pptx-tool.ts   # generate_pptxツール(スキーマ検証内蔵)
│   └── system-prompt.ts        # システムプロンプト
├── agent-run/
│   └── run.ts                  # ヘッドレスランナー
├── app/                        # 第32回から移植した対話UI(Next.js + CopilotKit)
│   ├── api/copilotkit/route.ts # CopilotKitランタイム(3ワークスペース分のエージェントを公開)
│   ├── components/             # スライドプレビュー・ツール呼び出し表示
│   ├── page.tsx                # 画面本体(ワークスペース切り替え付き)
│   └── variants.ts             # ワークスペース一覧の共有定数
├── workspaces/
│   ├── baseline/               # 機構のみのスキル
│   ├── improvement-1/          # +スライド設計ガイド
│   └── improvement-2/          # +保存前の事実確認
├── eval/
│   ├── cases.py                # results/*.json + ar5iv本文 → LLMTestCase
│   ├── metrics.py              # 搭載済みメトリクスの組み立て
│   └── run_eval.py             # evaluate() 実行・スコア保存
├── results/
│   ├── baseline/               # ランナー成果物
│   ├── improvement-1/
│   ├── improvement-2/
│   └── eval/                   # 評価スコアと理由
├── docs/                       # ツールのインストール手順
├── package.json
├── pyproject.toml
└── .env.sample
```

## 確認コマンド

```bash
npm run check
```

## 参考リンク

- [DeepEval documentation](https://deepeval.com/docs/getting-started)
- [LangChain JS DeepAgents docs](https://docs.langchain.com/oss/javascript/deepagents/overview)
- [OpenRouter DeepSeek V4 Flash](https://openrouter.ai/deepseek/deepseek-v4-flash)
- [ar5iv](https://ar5iv.labs.arxiv.org/)
