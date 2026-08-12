# Weave評価ハンズオン構成案

## 概要

「arXiv論文からプレゼンスライドを生成するAIエージェント」を題材に、W&B Weaveを使って次の改善ループを体験する。

```text
Traceで挙動を見る
  ↓
Datasetで評価対象を固定する
  ↓
Scorerで品質を定義する
  ↓
Evaluationで測る
  ↓
Compareで改善を確認する
  ↓
悪化した例のTraceへ戻る
```

本編では、Weave EvaluationがDatasetの各行をModelへ渡し、その場でエージェント出力を生成してscorerで採点する標準的なライブ評価を扱う。PythonとTypeScriptのプロセス境界や一時ワークスペースの管理は完成済みコードとして提供し、参加者はDataset、Model、Scorer、Evaluation、Traceの関係に集中する。

保存済み出力の再採点は、scorerやrubricの調整、judgeのばらつき確認、API障害時のフォールバックに有用だが、標準Evaluationを理解した後の追加課題とする。

## ハンズオンのメッセージ

このハンズオンで伝える中心的なメッセージは次の3点である。

1. Traceは「なぜその出力になったか」を調べるために使う。
2. Evaluationは「変更によって品質が改善したか」を同じ条件で測るために使う。
3. 集計スコアだけで判断せず、Datasetの行単位の結果とTraceを往復して改善箇所を探す。

## 学習目標

受講後、参加者が次を説明・実行できる状態を目指す。

- WeaveのTrace、Dataset、Model、Scorer、Evaluationの役割を説明できる。
- エージェントのモデル・ツール・SubAgent呼び出しをTraceで確認できる。
- 評価対象をバージョン付きDatasetとして管理できる。
- 既存のDeepEval metricをWeave scorerとして利用できる。
- `Evaluation(trials=N)`が各Dataset行についてModel実行からscorer実行までを反復することを説明できる。
- `trials`による出力とスコアのばらつきを確認できる。
- 同じDatasetとscorerを使って複数variantを比較できる。
- 悪化したDataset行から、関連するAgent Traceを調査できる。
- ライブ評価と保存済み出力の再採点の用途の違いを説明できる。

## 題材となる改善ループ

本リポジトリには、スライド生成スキルの作り込み段階が3つある。

```text
baseline
  スライド生成の基本機構
    ↓ スライド設計ガイドを追加
improvement-1
  詰め込み禁止・主張型タイトル・論理的な流れ
    ↓ 保存前の事実確認を追加
improvement-2
  本文照合・一般化禁止・未確認の数値を記載しない
```

期待する観察は次のとおり。

- `baseline`から`improvement-1`では、主にSlide Qualityが改善する。
- `improvement-1`から`improvement-2`では、主にSummarizationの忠実性が改善する。
- Tool Correctnessは、必要なツール呼び出しが維持されていることを確認する。

## 本編のアーキテクチャ

エージェント実行をEvaluationのModel呼び出しに含める。

```text
uv run eval/run_eval.py
  ├─ 共通のWeave Datasetを取得
  ├─ SlideAgentModel.predict()を各Dataset行・各trialで呼ぶ
  ├─ TypeScriptエージェントを一時ワークスペースで実行
  ├─ Agent Traceを送信し、conversation_idを出力へ含める
  ├─ DeepEval metricをWeave scorerとして実行
  └─ Weave Evaluationへ結果を記録
```

`results/`にある保存済み出力は本編の評価入力には使用しない。ライブ評価によって、Dataset、Model、Scorer、Evaluationの標準的な関係と、Evaluation結果からその実行で作られたTraceへ戻る流れを一貫して体験する。

## 所要時間

本編は約85分を想定する。環境構築とAPIキー発行は事前に完了している前提とする。

| セクション | 内容 | 目安 |
|---|---|---:|
| 導入 | Weaveの評価データモデル | 5分 |
| Observe | 事前取得したAgent Traceの確認 | 10分 |
| Dataset | 評価対象の固定とバージョン管理 | 10分 |
| Scorer | DeepEval adapterの実装 | 20分 |
| Evaluation | 1論文のライブ評価とtrialsの確認 | 20分 |
| Compare | 3 variantの比較 | 15分 |
| Debug | Evaluation行からTraceを調査 | 10分 |

全件のライブ評価と保存済み出力の再採点は任意の追加課題とする。

## 前提条件

- Node.js v22以上
- `uv`
- OpenRouter APIキー
- W&B APIキー
- W&Bのentityまたはteam
- リポジトリの依存関係がインストール済みであること

`.env`には次を設定する。

```env
OPENROUTER_API_KEY=...
WANDB_API_KEY=...
WEAVE_PROJECT=team/evals-seminar-20260910
```

Weaveを主題とするため、本ハンズオンでは`WANDB_API_KEY`を必須とする。

## 0. 導入：Weaveの評価データモデル

最初に、各要素の責務を短く説明する。

| 要素 | このハンズオンでの役割 |
|---|---|
| Trace | エージェントのモデル・ツール・SubAgent呼び出し |
| Dataset | 3本の論文と固定した評価用本文 |
| Model | variantごとのエージェント処理と追跡対象の設定 |
| Scorer | DeepEval metricを呼び出す評価処理 |
| Evaluation | Dataset × Model × Scorerの実行記録 |
| Compare | baselineと改善版の集計・行単位比較 |

評価の目的は単一の総合点を作ることではなく、変更がどの品質軸とどの入力例に影響したかを確認することだと説明する。

## 1. Observe：Agent Traceを見る

### 確認

講師が事前に取得したbaselineのAgent Traceを開く。参加者自身のライブ実行はEvaluationセクションで行い、同じエージェントを本編中に重複実行しない。

### 確認項目

Weave Agents画面で次を確認する。

- 1つのconversationに2つのTurnがあること。
- 論文URLを渡すターンと、スライド生成を承認するターンがあること。
- モデル呼び出しとトークン使用量。
- `execute`や`generate_pptx`などのツール呼び出し。
- SubAgent呼び出し。
- エラーとレイテンシ。

### 学習ポイント

- 最終スライドだけでは、失敗原因や不要な処理は分からない。
- Traceによって、出力に至るまでの経路を確認できる。
- 同じエージェントでも、入力や実行ごとに軌跡が変わり得る。

### フォールバック

API障害や時間超過に備え、講師が事前に取得したTrace、Evaluation、Compare画面のスクリーンショットを用意しておく。`results/`の保存済み出力も再採点用の補助データとして保持するが、本編の標準Evaluationの入力にはしない。

## 2. Dataset：評価対象を固定する

3本の論文を共通のWeave Datasetとして登録する。

```python
dataset = weave.Dataset(
    name="evals-seminar-20260910",
    rows=[
        {
            "arxiv_id": "1706.03762",
            "paper_url": "https://arxiv.org/abs/1706.03762",
            "source_text": "...評価用に固定した論文本文...",
            "expected_tools": ["execute", "generate_pptx"],
        },
        # 残り2論文
    ],
)

dataset_ref = weave.publish(dataset)
print(dataset_ref.uri())
```

### Datasetに含めるもの

- `arxiv_id`
- `paper_url`
- 評価基準として固定した`source_text`
- 期待するツール名`expected_tools`

### Datasetに含めないもの

- `variant`
- 生成済みスライド
- scorerの結果

variantや生成結果をDatasetへ含めると、baselineと改善版で入力条件が変わり、公平な比較が難しくなる。同じDataset version refをすべてのEvaluationで使用する。

### 学習ポイント

- Datasetは単なるPythonの配列ではなく、評価条件を固定するバージョン付き資産である。
- Datasetの変更とModelの変更を区別する。
- 異なるDatasetで得た集計値は、単純に比較しない。

## 3. Scorer：DeepEval metricをWeaveへ統合する

### 共通adapter

DeepEvalの`BaseMetric`のうち、`LLMTestCase`を入力とする単一ターンmetricをWeaveのfunction-based scorerへ変換する関数を実装する。関数名は短く保ち、`BaseConversationalMetric`や`BaseArenaMetric`が対象外であることはdocstringと型ヒントで明示する。

```python
def deepeval_metric_to_weave_scorer(
    *,
    name,
    metric_factory,
    test_case_factory,
):
    """`LLMTestCase`を使う単一ターンmetricだけを変換する。"""

    @weave.op(name=name)
    async def scorer(
        output: dict,
        source_text: str,
        expected_tools: list[str],
    ) -> dict:
        metric = metric_factory()
        test_case = test_case_factory(
            output=output,
            source_text=source_text,
            expected_tools=expected_tools,
        )

        score = await metric.a_measure(test_case)
        return {
            "score": float(score),
            "passed": metric.is_successful(),
            "reason": metric.reason,
        }

    return scorer
```

metricインスタンスは評価結果を内部状態として保持するため、使い回さず、scorer呼び出しごとに`metric_factory`から生成する。

### 3つのscorer

同じadapterから次を作る。

```python
scorers = [
    deepeval_metric_to_weave_scorer(
        name="tool_correctness",
        metric_factory=build_tool_correctness_metric,
        test_case_factory=build_slide_test_case,
    ),
    deepeval_metric_to_weave_scorer(
        name="summarization",
        metric_factory=build_summarization_metric,
        test_case_factory=build_slide_test_case,
    ),
    deepeval_metric_to_weave_scorer(
        name="slide_quality",
        metric_factory=build_slide_quality_metric,
        test_case_factory=build_slide_test_case,
    ),
]
```

scorer名は`tool_correctness`、`summarization`、`slide_quality`のような安定した論理名とし、`v1`のような手動バージョンを付けない。Weaveは`@weave.op`のコードが変わると、同じOp名の新しいバージョンを自動作成する。異なるrubricを同じEvaluationで比較するときは、`summarization_balanced`と`summarization_strict`のように評価基準の意味を表す別名を使う。

環境変数や外部ファイルなど、コード変更として検出されない可能性があるjudge設定はEvaluation metadataにも記録する。DeepEvalのバージョンはlockfileで固定し、rubricやassessment questionsはコードまたはバージョン付きオブジェクトとして管理する。

### 学習ポイント

- Weaveは評価アルゴリズムを限定せず、既存の評価器をscorerとして統合できる。
- scorerは数値だけでなく、pass/failや判定理由を返せる。
- scorerの論理名とWeaveが自動生成するOp versionを区別する。
- rubric、judge model、依存バージョンなど、スコアの意味を決める設定を追跡可能にする。
- DeepEvalのAPIエラーを0点として扱わず、Evaluation errorとして区別する。

## 4. Evaluation：評価の中でエージェントを実行する

### Model

variantごとのエージェントをWeave Modelとして表現する。PythonとTypeScriptのプロセス境界、一時ワークスペース、JSONプロトコルは完成済みコードとして提供する。

```python
class SlideAgentModel(weave.Model):
    variant: str

    @weave.op()
    async def predict(self, arxiv_id: str, paper_url: str) -> dict:
        return await run_agent_process(
            variant=self.variant,
            arxiv_id=arxiv_id,
            paper_url=paper_url,
        )
```

ハンズオンで使用するGit commitと依存関係のlockfileを講師側で固定する。Modelでは比較軸である`variant`だけを明示し、参加者がWeaveの基本的なデータモデルに集中できるようにする。

### Evaluation

```python
evaluation = weave.Evaluation(
    evaluation_name="evals-seminar-20260910",
    dataset=dataset,
    scorers=scorers,
    trials=1,
)

await evaluation.evaluate(
    SlideAgentModel(variant="baseline"),
    __weave={"display_name": "baseline"},
)
```

参加者は、まず1論文・1 variant・1 trialで実行する。

```bash
uv run eval/run_eval.py baseline \
  --arxiv-id 1706.03762 \
  --trials 1
```

### trialsの意味

Weaveの`Evaluation(trials=N)`は、各Dataset行についてModel実行と全scorer実行をN回繰り返す。CLIの`--trials`をそのまま`Evaluation.trials`へ渡す。

```text
trial 1: エージェント生成 -> scorer実行
trial 2: エージェント生成 -> scorer実行
trial 3: エージェント生成 -> scorer実行
```

ライブ評価ではtrialごとにエージェント出力が生成され、その出力を各scorerが1回ずつ採点する。保存済み出力の再採点ではModelが毎回同じ出力を返すため、複数trialによって同一出力に対するscorer結果のばらつきを確認できる。

### 確認項目

- EvaluationのDataset ref。
- Modelのvariant。
- scorerの名前とバージョン。
- 論文ごとのスコアと理由。
- `trials`がModel実行とscorer実行の両方を反復すること。
- errored rowの有無。
- Model出力に含まれるconversation ID。

## 5. Compare：改善版を比較する

同じDatasetとscorerを使って3 variantを評価する。

```bash
uv run eval/run_eval.py baseline --trials 1
uv run eval/run_eval.py improvement-1 --trials 1
uv run eval/run_eval.py improvement-2 --trials 1
```

参加者は1論文の実装確認だけを行い、3論文 × 3 variantの完成結果には講師が同じライブModel、Dataset、scorerで事前実行したEvaluationも利用する。上記コマンド名は実装後の想定である。

WeaveのEvals画面で3つのEvaluationを選び、Compareを開く。

### 確認項目

- Slide Qualityは`improvement-1`で上がったか。
- Summarizationは`improvement-2`で上がったか。
- Tool Correctnessに回帰がないか。
- すべての論文で改善しているか、一部だけか。
- 改善幅はtrial間のばらつきより大きいか。
- scorerの理由は、実際のスライド変更と整合しているか。

### 学習ポイント

- 平均スコアだけでは、特定の入力で起きた回帰を見落とす。
- 比較には同じDatasetとscorer versionが必要である。
- composite scoreだけでなく、品質軸ごとの変化を見る。

## 6. Debug：EvaluationからTraceへ戻る

スコアが低い、または改善版で悪化した論文を1つ選ぶ。

```text
Evaluationで低スコアの行を特定
  ↓
Model出力のconversation_idを確認
  ↓
Agent Traceを開く
  ↓
モデル・ツール・SubAgent呼び出しを調査
```

### 調査例

- 論文取得が途中で失敗していないか。
- SubAgentが本文にない一般知識を追加していないか。
- `generate_pptx`前の確認が不足していないか。
- 不要なツール呼び出しが増えていないか。
- 長い入力が切り詰められていないか。

初期実装ではPythonのEvaluation TraceとTypeScriptのAgent Traceをプロセス境界をまたいだ親子Traceにはしない。Model出力へ含めた`conversation_id`で対応付ける。直接リンクやトレースコンテキスト伝播は発展編で扱う。

## 発展編：保存済み出力を再採点する

### 目的

エージェントを再実行せず、保存済みの同一出力に対してscorerやrubricを低コストで調整する。過去の出力を新しいscorerで再採点するときや、judgeのばらつきだけを調べるときに利用する。

```python
class RecordedSlideAgentModel(weave.Model):
    variant: str

    @weave.op()
    def predict(self, arxiv_id: str) -> dict:
        return load_recorded_output(
            variant=self.variant,
            arxiv_id=arxiv_id,
        )
```

### ライブ評価との違い

| 観点 | Live Evaluation | Recorded-output rescoring |
|---|---|---|
| Model出力 | trialごとに生成 | 固定 |
| `Evaluation(trials=N)` | エージェントとscorerの反復 | 固定出力を返すModelとscorerの反復 |
| コスト | 大きい | 小さい |
| 再現性 | 生成のばらつきを含む | 高い |
| 主な用途 | システム回帰評価 | scorer開発・過去出力の再採点 |

Recorded Modelを使う場合も、`Evaluation(trials=N)`の意味は変わらない。Modelが毎回同じ値を返すため、結果として同一出力をscorerが繰り返し採点する。反復数は本編と同じ`--trials`で指定する。

保存済み予測を既存の処理フローから逐次記録したい場合は、Weaveの`EvaluationLogger`も選択肢として紹介する。

この内容はWeave Evaluationの標準フローを理解した後に、ライブ評価と再採点を使い分けるための追加課題とする。

## 実装時のファイル構成案

```text
eval/
  cases.py               # Dataset行とLLMTestCaseの組み立て
  metrics.py             # DeepEval metric factory
  weave_scorer.py        # DeepEval -> Weave scorer adapter
  agent_model.py         # SlideAgentModelとsubprocess境界
  run_eval.py            # Weave Evaluation runner

agent-run/
  runner.ts              # runSlideAgent()本体
  eval.ts                # 評価用JSON入出力エントリポイント

docs/
  weave-hands-on-plan.md
```

保存済み出力の再採点を実装する場合だけ、Recorded Modelを追加する。

```text
eval/
  recorded_model.py      # RecordedSlideAgentModel
  run_rescore.py         # 保存済み出力の再採点runner
```

## 講師側の事前準備

- W&B projectを作成する。
- 3本の論文をWeave Datasetとしてpublishする。
- Datasetのversion refを固定する。
- 1論文・1 trialの参加者向けコマンドをスモークテストする。
- 3 variantのライブAgent Traceを最低1件ずつ用意する。
- 同じDatasetとscorer versionで3つのライブEvaluationを事前実行し、Compare画面が表示できることを確認する。
- 各Evaluation行の`conversation_id`から対応するAgent Traceを特定できることを確認する。
- 再採点の追加課題用に`results/`の3論文 × 3 variantを確認する。
- API障害時に見せるスクリーンショットまたは保存済みビューを用意する。
- エージェント生成とscorer実行の想定コスト、所要時間を確認する。

## コストと時間の調整

全参加者が3論文 × 3 variant × 複数trialsを実行すると、エージェント生成とscorerのコスト、待ち時間が大きくなる。

本編では次の進め方を推奨する。

1. 参加者は1論文・1 variant・`trials=1`で実装確認する。
2. `trials=3`は講師の事前実行結果で分布を確認する。時間と予算に余裕がある場合だけ参加者も実行する。
3. 3論文 × 3 variantの完成結果は講師が事前実行したライブEvaluationを利用する。
4. 保存済み出力に対するscorerのばらつきは、再採点の追加課題で`trials=3`として確認する。
5. ライブ実行に失敗した場合も、事前取得したEvaluationとTraceでCompare、Debugまで進める。

## 完了条件

ハンズオン本編の完了条件は次のとおり。

- Agent Traceでモデル・ツール・SubAgent呼び出しを確認できた。
- 共通Datasetのversion refを確認できた。
- DeepEval metricを共通adapterでWeave scorerへ変換できた。
- `SlideAgentModel`を使い、1論文についてEvaluation内でエージェントを実行できた。
- `Evaluation(trials=N)`がModel実行からscorer実行までを反復することを説明できた。
- `--trials`がWeaveの`Evaluation.trials`へ対応することを説明できた。
- 3 variantをCompare evaluationsで比較できた。
- 低スコアまたは回帰した行を1件説明できた。
- Evaluation行の`conversation_id`からAgent Traceを特定し、原因候補を1件以上挙げられた。

## 参考資料

- [Weave Evaluations overview](https://docs.wandb.ai/weave/guides/core-types/evaluations)
- [Weave Scoring overview](https://docs.wandb.ai/weave/guides/evaluation/scorers)
- [Weave Datasets](https://docs.wandb.ai/weave/guides/core-types/datasets)
- [Weave Compare evaluations](https://docs.wandb.ai/weave/guides/evaluation/compare_evals)
- [Weave EvaluationLogger](https://docs.wandb.ai/weave/guides/evaluation/evaluation_logger)
- [Weave OpenRouter integration](https://docs.wandb.ai/weave/guides/integrations/openrouter)
- [DeepEval custom metrics](https://deepeval.com/docs/metrics-custom)
