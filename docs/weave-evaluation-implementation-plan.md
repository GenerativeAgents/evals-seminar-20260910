# Weaveを使ったライブバッチ評価の実装方針

## 目的

現在のDeepEvalベースの評価指標を維持しながら、評価の実行・記録・比較をW&B Weaveの`Evaluation`で行う。ハンズオン本編では、Datasetの各行をModelへ渡し、その場でエージェント出力を生成してscorerで採点するWeaveの標準フローを扱う。

ここでいう「オフライン評価」は、保存済みの生成結果を再採点することではなく、本番トラフィックとは独立した評価用データセットに対して、その評価試行の中でエージェントを実行して採点するバッチ評価を指す。標準ライブ評価では`results/<variant>/`にある過去の結果を評価入力として使用しない。

なお、エージェントによる論文取得、OpenRouterによる生成・採点、Weaveへの記録にはネットワーク接続が必要であり、完全なネットワーク非接続実行ではない。

## 採用する方針

- Pythonを評価のオーケストレーターとして維持し、`weave.Evaluation`を使用する。
- WeaveのModelからTypeScriptエージェントをその場で起動する。
- エージェントの出力は各run workspaceの`evaluation-result.json`から受け取り、`results/`から読み込まない。
- `workspaces/<variant>/`は実行先ではなく読み取り専用のworkspace templateとして扱い、CLI、Web UI、評価は共通の初期化処理でrun workspaceを作る。
- DeepEvalの各metricは、共通の変換関数でWeaveのfunction-based scorerへ変換する。
- `ToolCorrectnessMetric`、`SummarizationMetric`、`GEval`の定義とスコア尺度は維持する。
- 全variantで同じDatasetと同じscorerバージョンを使用し、WeaveのCompare evaluationsで比較できるようにする。
- 反復回数にはWeave標準の`Evaluation(trials=N)`だけを使用する。
- PythonとTypeScriptのプロセス境界、一時ワークスペース、JSONプロトコルは完成済みコードとして提供し、ハンズオン参加者の実装対象にはしない。
- 参加者は1論文・1 variant・1 trialでライブ評価を確認し、全件比較には講師が事前実行したEvaluationも利用する。
- 保存済み出力の再採点は、scorer開発、rubric変更後の再評価、障害時フォールバックのための追加機能として本編から分離する。

## 全体構成

```text
Weave Dataset
  arxiv_id / paper_url / source_text / expected_tools
       |
       v
Weave Evaluation
       |
       v
SlideAgentModel.predict()
       |
       v
TypeScriptエージェントを評価試行内で実行
       |
       v
slides / tool_calls / final_text / conversation_id
       |
       +-- Weave scorer -> DeepEval ToolCorrectnessMetric
       +-- Weave scorer -> DeepEval SummarizationMetric
       +-- Weave scorer -> DeepEval GEval
```

Weave Evaluationには、Dataset、Model、scorerのバージョンと、各試行の入力・出力・スコア・理由を記録する。既存のAgent Traceは同じWeave projectのAgents画面に記録し、`conversation_id`でEvaluationの行と対応付ける。

## Dataset

Datasetの各行は、評価対象の論文と採点基準だけを保持する。

```python
{
    "arxiv_id": "1706.03762",
    "paper_url": "https://arxiv.org/abs/1706.03762",
    "source_text": "...評価基準として固定した論文本文...",
    "expected_tools": ["execute", "generate_pptx"],
}
```

variantや生成結果はDatasetに含めない。`baseline`、`improvement-1`、`improvement-2`でDatasetが分かれると、入力条件が同一であることを保証できず、Weaveの比較でもdataset inconsistencyになるためである。

`source_text`はエージェントへの入力ではなく、DeepEvalの`SummarizationMetric`と`GEval`が参照する固定の評価基準として使用する。エージェント自身には`paper_url`を渡し、現在と同様に実行中に論文を取得させる。

Datasetは`weave.Dataset`として名前を付けてpublishし、variant間の比較では同じversion refを使う。ar5ivの内容が後日変わっても過去の評価を再現できるように、取得済みの本文をDatasetのバージョンに含める。

### ハンズオン用の1行スモーク評価

参加者が全3論文を生成せずに標準フローを確認できるよう、runnerは`--arxiv-id`で共通Datasetの1行を選択できるようにする。選択した行から1行のEvaluation datasetを構成し、Evaluation metadataへ次を記録する。

- 元のDataset version ref
- 選択した`arxiv_id`
- `scope="smoke"`

この1行Evaluationは動作確認用であり、3 variantの正式な集計比較には使用しない。講師が事前実行する比較では、全variantに同一の3行Dataset version refをそのまま渡し、`scope="full"`を記録する。異なるscopeやDatasetの集計値を比較しない。

## ライブエージェントの実行

### エージェント本体とCLIを分離する

現在の[`agent-run/run.ts`](../agent-run/run.ts)は、エージェント実行と`results/`への保存を同時に行っている。これを次の2層に分ける。

1. `runSlideAgent()`
   - `arxivId`と`variant`を受け取る。
   - 2ターンのエージェント実行を行う。
   - スライド、ツール呼び出し、最終メッセージ、処理時間、Agent Traceのconversation IDを返す。
   - `results/`には書き込まない。
2. 手動実行用CLI
   - `runSlideAgent()`を呼ぶ。
   - 現在の`npm run agent -- <id> <variant>`との互換性を維持する。
   - 手動実行時のみ、必要に応じて`results/<variant>/<id>.json`へ保存する。

評価用エントリポイントは、Pythonからコマンドライン引数でarXiv ID、論文URL、variant、run IDを受け取る。Python側は`asyncio.create_subprocess_exec()`でこれを起動し、TypeScript側はrun IDに対応するrun workspaceへ`evaluation-result.json`を保存する。stdoutとstderrはログ専用とし、Pythonはプロセス終了後に結果ファイルを検証して読み取る。

### スライドを直接取得する

[`agent/generate-pptx-tool.ts`](../agent/generate-pptx-tool.ts)は、成功時のツール結果に検証済みのスライドを含めている。評価実行では`generate_pptx`の`on_tool_end`または対応するToolMessageを捕捉し、その結果を直接`runSlideAgent()`の返却値にする。

これにより、生成後に`results/`や共有されたslidesファイルを読み直す必要がなくなる。

```json
{
  "generationSuccess": true,
  "slides": {},
  "slidesText": "...",
  "toolCalls": [],
  "finalText": "...",
  "durationMs": 12345,
  "conversationId": "baseline:..."
}
```

### workspace templateとrun workspaceを分離する

現在のCLIは実行ごとにagentとthread IDを作る一方、ファイルの書き込み先には固定の`workspaces/<variant>/`を使用する。Web UIもvariantごとに作成したagentが同じディレクトリを共有する。そのため、同じ論文の再実行や複数trial・複数会話の並列実行ではファイルが競合し、過去のファイルを誤って利用する可能性がある。

今後は`workspaces/<variant>/`を設定とスキルを保持する読み取り専用のworkspace templateとし、実行時に共通のworkspace初期化処理で`./tmp/workspaces/`配下へrun workspaceを作る。ディレクトリ名はUTCの生成時刻、variant、一意なrun IDから構成する。

```text
./tmp/workspaces/<yyyyMMddHHmmss>-<variant>-<run-id>/
  AGENTS.md
  .agent/skills/
  slides/             # 空のディレクトリ
  large_tool_results/ # 空のディレクトリ
  evaluation-result.json # Evaluation実行完了時のみ
```

たとえば、`./tmp/workspaces/20260812153045-baseline-a1b2c3d4/`のようになる。時刻だけでは同じ秒に開始した同一variantの実行が衝突するため、`run-id`を省略しない。`variant`は既知の値から選択し、パス要素として安全な文字列であることを初期化前に検証する。

初期化処理は次を行う。

1. `workspaces/<variant>/AGENTS.md`と`.agent/skills/`の存在を検証する。
2. 一意なrun workspaceを`./tmp/workspaces/`配下に作る。
3. templateから`AGENTS.md`と`.agent/skills/`だけをコピーする。
4. 空の`slides/`と`large_tool_results/`を作る。
5. run workspaceをrootにした独立した`LocalShellBackend`とagentを作る。

template内の既存`slides/`や`large_tool_results/`はコピーしない。共有ディレクトリを実行前に削除・初期化する方式も、並列実行と競合するため採用しない。

run workspaceの分離単位は実行経路ごとに次のようにする。

| 実行経路 | 分離単位 |
| --- | --- |
| Weave Evaluation | Dataset行の各trial |
| 手動CLI | コマンド実行 |
| Web UI | conversation / thread |

Web UIでは1回のHTTP requestごとではなく、同じ会話の複数ターンで同じrun workspaceを再利用する。現在のvariant単位でagentを保持する構成は、conversation IDをキーにagent、backend、run workspaceを管理する構成へ変更する。

共通処理として、概念的に`createRunWorkspace(variant, runId)`を用意する。agent生成処理は作成済みrun workspaceを受け取り、templateのコピーを担当しない。エージェントの結果は戻り値、`results/`、またはWeb UIの状態へ渡し、元の`workspaces/`を変更しない。

run workspaceは実行後も残し、自動削除やTTL cleanupは実装しない。これにより、生成途中のファイルやツールの出力を実行後の調査に利用できる。`tmp/`配下の削除が必要になった場合の手動運用は、本実装の対象外とする。

`./tmp/`はリポジトリ直下に置き、`tmp/.gitkeep`だけをGit管理する。`.gitignore`では次のように一時生成物を除外する。

```gitignore
tmp/*
!tmp/.gitkeep
```

## Weave Model

Python側に、ライブエージェントを表す`weave.Model`を定義する。

```python
class SlideAgentModel(weave.Model):
    variant: str

    @weave.op()
    async def predict(self, arxiv_id: str, paper_url: str) -> dict:
        return await run_agent_process(
            arxiv_id=arxiv_id,
            paper_url=paper_url,
            variant=self.variant,
        )
```

評価対象の再現性は、ハンズオンで使用するGit commitと依存関係のlockfileを講師側で固定することで担保する。Modelのフィールドは比較軸である`variant`だけとし、外部スキルファイルのhash計算は実装しない。

プロセスの起動失敗、タイムアウト、JSONプロトコル違反はインフラエラーとして例外にし、Weave上でもtrialをerrorにする。

エージェントが正常終了したものの`generate_pptx`を呼ばなかった、または不正なスライドを生成した場合は、エージェントの振る舞いとして評価できるよう構造化された失敗出力を返す。DeepEval scorerは、採点に必要な`actual_output`が存在しない場合、理由を付けてfailとして扱う。

## DeepEvalの`LLMTestCase` metricからWeave scorerへの変換

### 対象とする共通インターフェース

プロジェクトで固定している`deepeval==4.0.7`では、現在使用中の次のmetricはすべて`BaseMetric`を継承する。

- `ToolCorrectnessMetric`
- `SummarizationMetric`
- `GEval`

共通して次のインターフェースを持つ。

```python
metric.measure(test_case: LLMTestCase) -> float
await metric.a_measure(test_case: LLMTestCase) -> float

metric.score
metric.reason
metric.error
metric.score_breakdown
metric.is_successful()
```

そのため、metricごとにWeave scorerクラスを実装せず、`LLMTestCase`を入力とするmetricに対象を限定した変換関数を1つ実装する。

### 変換関数

```python
import asyncio
from collections.abc import Callable

import weave
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

MetricFactory = Callable[[], BaseMetric]
TestCaseFactory = Callable[..., LLMTestCase]


def deepeval_metric_to_weave_scorer(
    *,
    name: str,
    metric_factory: MetricFactory,
    test_case_factory: TestCaseFactory,
):
    """`LLMTestCase`を使う単一ターンmetricをWeave scorerへ変換する。

    `BaseConversationalMetric`と`BaseArenaMetric`は対象外。
    """

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

        if metric.async_mode:
            measured_score = await metric.a_measure(test_case)
        else:
            measured_score = await asyncio.to_thread(
                metric.measure,
                test_case,
            )

        if metric.error:
            raise RuntimeError(metric.error)

        score = metric.score if metric.score is not None else measured_score
        result = {
            "score": float(score),
            "passed": metric.is_successful(),
        }

        optional_fields = {
            "reason": metric.reason,
            "score_breakdown": metric.score_breakdown,
            "evaluation_cost": metric.evaluation_cost,
            "input_tokens": metric.input_tokens,
            "output_tokens": metric.output_tokens,
        }
        result.update(
            {
                key: value
                for key, value in optional_fields.items()
                if value is not None
            }
        )
        return result

    return scorer
```

metricインスタンスではなく`metric_factory`を受け取る。DeepEval metricは`score`、`reason`、`success`などを自身に保持するため、同じインスタンスを並列評価で使い回すと結果が混ざる可能性がある。各scorer呼び出しで新しいmetricを生成する。DeepEvalのAPIエラーは0点へ変換せず、そのscorer呼び出しをerrorにする。

### 共通のテストケース変換

現在の3 metricが必要とするフィールドをすべて含んだ`LLMTestCase`を作る。

```python
from deepeval.test_case import LLMTestCase, ToolCall


def build_slide_test_case(
    *,
    output: dict,
    source_text: str,
    expected_tools: list[str],
) -> LLMTestCase:
    return LLMTestCase(
        input=source_text,
        actual_output=output["slides_text"],
        tools_called=[
            ToolCall(name=call["name"])
            for call in output["tool_calls"]
        ],
        expected_tools=[
            ToolCall(name=name)
            for name in expected_tools
        ],
    )
```

### scorerの生成

[`eval/metrics.py`](../eval/metrics.py)の`build_metrics()`は、metricごとのfactoryへ分割する。

```python
tool_correctness_scorer = deepeval_metric_to_weave_scorer(
    name="tool_correctness",
    metric_factory=build_tool_correctness_metric,
    test_case_factory=build_slide_test_case,
)

summarization_scorer = deepeval_metric_to_weave_scorer(
    name="summarization",
    metric_factory=build_summarization_metric,
    test_case_factory=build_slide_test_case,
)

slide_quality_scorer = deepeval_metric_to_weave_scorer(
    name="slide_quality",
    metric_factory=build_slide_quality_metric,
    test_case_factory=build_slide_test_case,
)
```

scorerの`name`は論理名として固定し、`v1`のような手動バージョンを含めない。`@weave.op`はコード変更時に同じOp名の新しいバージョンを自動作成するため、`summarization_v1`、`summarization_v2`のように名前を変えると、同じscorerのバージョン履歴が別のOp系列へ分断される。

rubric、assessment questions、judge model設定など、スコアの意味を変える値は可能な限りコードまたはバージョン付きオブジェクトとして管理する。環境変数、外部ファイル、同じモデルIDの提供側更新など、Opのコード変更として検出されない可能性がある値はEvaluation metadataにも記録し、DeepEvalのバージョンはlockfileで固定する。同じEvaluation内で異なる評価基準を並べて比較する場合は、`summarization_balanced`、`summarization_strict`のように基準の意味を表す別名を使う。

この変換関数が保証する対象は、`BaseMetric`のうち`LLMTestCase`を入力とする単一ターンmetricまでとする。`BaseConversationalMetric`や`BaseArenaMetric`は入力型・返却型が異なるため、必要になった時点で別のadapterを追加する。関数名は利用時の読みやすさを優先して短く保ち、対応範囲はdocstring、型ヒント、テストで明示する。

## trialsの定義

反復回数にはWeave標準の`Evaluation(trials=N)`だけを使用する。各Dataset行について、Model実行と全scorer実行がN回繰り返される。CLIの`--trials`をそのまま`Evaluation.trials`へ渡す。

```bash
uv run eval/run_eval.py baseline \
  --trials 3
```

ライブ評価ではtrialごとにエージェント出力が変わり得る。保存済み出力の再採点ではRecorded Modelが毎回同じ値を返すため、複数trialによって同一出力に対するscorer結果のばらつきを確認できる。

## Evaluation runner

[`eval/run_eval.py`](../eval/run_eval.py)は次の責務を持つ。

1. `.env`を読み込む。
2. `WANDB_API_KEY`、`OPENROUTER_API_KEY`、`WEAVE_PROJECT`を検証する。
3. `weave.init(WEAVE_PROJECT)`を実行する。
4. 指定されたversionの共通Datasetを取得する。
5. `--arxiv-id`が指定された場合は該当する1行だけをスモーク評価用に選択する。
6. 指定されたvariantの`SlideAgentModel`を作る。
7. DeepEval scorerを作る。
8. `--trials`をWeaveの`Evaluation(trials=...)`へ渡して実行する。
9. WeaveのEvaluation URL、scope、Dataset ref、Model variant、集計結果を表示する。

概念的な実装は次の形になる。

```python
evaluation = weave.Evaluation(
    evaluation_name="evals-seminar-20260910",
    dataset=dataset,
    scorers=[
        tool_correctness_scorer,
        summarization_scorer,
        slide_quality_scorer,
    ],
    trials=args.trials,
)

result = await evaluation.evaluate(
    SlideAgentModel(variant=args.variant),
    __weave={"display_name": args.variant},
)
```

Evaluation metadataには、少なくとも`scope`、元のDataset ref、選択した`arxiv_id`、`trials`、judge model、rubric識別子、DeepEval versionを記録する。コードだけでは検出できない実行条件を比較時に確認できるようにする。Git commitはハンズオン環境全体の前提として固定し、Model固有のフィールドには含めない。

参加者向けの最小実行は次の形とする。

```bash
uv run eval/run_eval.py baseline \
  --arxiv-id 1706.03762 \
  --trials 1
```

講師の全件事前実行では`--arxiv-id`を指定しない。全variantで同じDataset version ref、scorer version、trialsを使用する。

Weaveを評価結果の正本とする。ローカルJSONが必要な場合は、Evaluation完了後にWeave Evaluation APIからエクスポートする機能を別途追加し、評価入力としては使用しない。

## 保存済み出力の再採点

保存済み出力の再採点は、本編の`run_eval.py`とは別の`run_rescore.py`として実装する。標準ライブ評価と再採点のコマンドを分け、保存済み出力の検索器を通常のエージェントModelと誤認しにくくする。

```python
class RecordedSlideAgentModel(weave.Model):
    variant: str
    source_manifest_hash: str

    @weave.op()
    def predict(self, arxiv_id: str) -> dict:
        return load_recorded_output(
            variant=self.variant,
            arxiv_id=arxiv_id,
        )
```

再採点でもライブ評価と同じ`--trials`を`Evaluation.trials`へ渡す。Recorded Modelは毎回同じ出力を返すため、`trials>1`では同一出力をscorerが繰り返し採点する。入力ファイル一覧と内容から`source_manifest_hash`を計算し、どの保存済み出力を採点したかを追跡できるようにする。

```bash
uv run eval/run_rescore.py baseline --trials 3
```

## Agent Traceとの関係

既存のTypeScript Agent TraceとPythonのWeave Evaluationは、同じproject内でも別の表示面とトレース系統になる。初期実装ではプロセスをまたいだ親子トレース化までは行わない。

代わりに、エージェント実行時に使用した`conversation_id`をModelの出力へ含める。これにより、Evals画面の失敗行からAgents画面の詳細なモデル・ツール・SubAgent呼び出しへ移動できる。

将来、Evaluationの`model.predict`配下へエージェント内部呼び出しを完全にネストしたい場合は、標準Weave callの計装またはプロセス間のトレースコンテキスト伝播を別タスクとして検討する。

## 変更対象

想定する主な変更は次のとおり。

```text
agent-run/
  run.ts                 # 手動CLIを薄いラッパーへ変更
  runner.ts              # runSlideAgent()本体を新設
  eval.ts                # 評価用引数・結果ファイル境界を新設

agent/
  run-workspace.ts       # templateからrun workspaceを作成する共通処理

app/api/copilotkit/
  route.ts               # conversation単位のagent/backend/workspace管理へ変更

eval/
  cases.py               # Dataset行とLLMTestCaseの組み立て
  metrics.py             # metricごとのfactoryへ分割
  weave_scorer.py        # DeepEval -> Weave変換関数を新設
  agent_model.py         # SlideAgentModelとsubprocess境界を新設
  run_eval.py            # weave.Evaluationベースへ変更
  recorded_model.py      # 保存済み出力の再採点用Model（追加課題）
  run_rescore.py         # 保存済み出力の再採点runner（追加課題）

pyproject.toml           # weave依存を追加、DeepEvalは維持

tmp/
  .gitkeep               # run workspace親ディレクトリだけをGit管理

docs/
  weave-hands-on-plan.md # ライブEvaluation中心の教材構成
```

ファイル名は実装時に既存構成との整合性を見て調整してよいが、TypeScriptのエージェント実行、PythonのWeave Model、DeepEval adapterの責務は分離する。

## 実装順序

1. workspace templateからrun workspaceを作成する共通処理を実装する。
2. `runSlideAgent()`を切り出し、CLIをコマンド実行単位のrun workspaceへ移行しながら現在の手動CLIの動作を維持する。
3. Web UIをconversation単位のagent/backend/run workspace管理へ移行する。
4. 評価用JSONプロトコルを実装する。
5. Pythonからエージェントを1回起動する`SlideAgentModel`を実装する。
6. DeepEval metric factoryと共通`LLMTestCase` builderへ整理する。
7. `deepeval_metric_to_weave_scorer()`を実装する。
8. `run_eval.py`を`weave.Evaluation`へ移行する。
9. 1論文・`trials=1`でスモークテストする。
10. 3論文・3 variantを同じDataset/scorer versionで実行し、Compare evaluationsを確認する。
11. Evaluation行の`conversation_id`からAgent Traceへ移動できることを確認する。
12. 追加課題として`RecordedSlideAgentModel`と`run_rescore.py`を実装する。
13. 必要に応じてWeave Evaluation APIからローカルJSONへエクスポートする機能を追加する。

## READMEの後続変更

READMEは本方針の変更時点では編集せず、ライブEvaluation実装とコマンドが確定した後に別作業として統一する。後続のREADME変更には少なくとも次を含める。

- 「評価は`results/<variant>/`だけを読む」という説明を削除する。
- 標準の評価コマンドを`run_eval.py`のライブ評価へ変更する。
- 参加者向けの1論文スモークコマンドと、講師向けの全件コマンドを分けて記載する。
- `--trials`をWeaveの`Evaluation.trials`へそのまま渡すことを説明する。
- `results/`は手動実行の成果物および`run_rescore.py`の追加課題用であり、標準ライブ評価の入力ではないことを明記する。
- WeaveをEvaluation結果の正本とし、ローカルJSONが必要な場合はEvaluation完了後にエクスポートする方針を記載する。
- Evaluation行の`conversation_id`からAgent Traceを調査する手順を追加する。
- `workspaces/<variant>/`はworkspace templateであり、CLI、Web UI、評価の実行時には`tmp/workspaces/`配下のrun workspaceを使用することを説明する。
- CLIはコマンド単位、Web UIはconversation単位、Evaluationはtrial単位でworkspaceを分離することを説明する。
- `tmp/`配下のrun workspaceは実行後も調査用に残り、自動削除しないことを説明する。
- `agent/run-workspace.ts`、`agent-run/runner.ts`、`agent-run/eval.ts`、`app/api/copilotkit/route.ts`、`eval/agent_model.py`、`eval/weave_scorer.py`、`eval/run_rescore.py`を反映してファイル構成を更新する。
- 評価の反復オプションは`--trials`だけを記載する。

## テスト方針

### 単体テスト

- adapterがscorer呼び出しごとに新しいDeepEval metricを生成すること。
- scorerの論理名に手動バージョンを含めず、コード変更が同じOp系列の新しいWeave versionとして記録されること。
- `score`、`passed`、`reason`、`score_breakdown`がWeave向け辞書へ変換されること。
- DeepEvalの例外を0点へ変換せず、scorer errorとして伝播すること。
- agent出力から`LLMTestCase`の`actual_output`、`tools_called`、`expected_tools`を正しく作ること。
- 評価用エントリポイントが指定run IDのworkspaceへ`evaluation-result.json`を書き、標準出力のログと混ざらないこと。
- run workspace名が`<yyyyMMddHHmmss>-<variant>-<run-id>`形式であり、同じ秒・同じvariantでも衝突しないこと。
- templateから必要な設定だけをコピーし、既存の`slides/`と`large_tool_results/`を引き継がないこと。
- CLIの各実行とEvaluationの各trialが異なるrun workspaceを使用すること。
- Web UIではconversation間でrun workspaceが分離され、同じconversationの複数ターンでは再利用されること。
- CLI、Evaluation、Web UIのrun workspaceが実行後も保持されること。
- 古いslidesファイルや`results/`が評価結果に混入しないこと。
- `--arxiv-id`によるスモーク評価が1行だけを実行し、元のDataset refとscopeをmetadataへ残すこと。

DeepEvalの実APIを呼ばないadapterテストでは、`BaseMetric`互換のfake metricを使用する。

### 結合テスト

- 1論文についてライブエージェントが起動されること。
- `generate_pptx`の検証済み出力がModel出力になること。
- Evaluationに3つのscorer列と理由が記録されること。
- Model出力の`conversation_id`からAgent Traceを特定できること。
- 同じDataset refとscorer versionでvariant間比較ができること。
- `run_rescore.py`がエージェントを起動せず、保存済み出力だけを指定した`Evaluation.trials`で再採点すること。

### 回帰確認

DeepEval metric自体は変更しないため、同一の`LLMTestCase`を旧コードとadapterへ渡し、スコアと理由が一致することを確認する。LLM judge由来の揺らぎがあるため、完全一致が必要な決定的metricと、許容差または複数回分布で比較するLLM metricを分ける。

## 完了条件

- 評価実行中に各Dataset行・各trialでエージェントが新規実行される。
- 標準の`run_eval.py`は`results/<variant>/`を評価入力として読み込まない。
- 並列trial間でワークスペースや生成ファイルが共有されない。
- `workspaces/<variant>/`が読み取り専用のworkspace templateとして扱われ、CLI、Web UI、評価から変更されない。
- CLIは実行単位、Web UIはconversation単位、Evaluationはtrial単位で`tmp/workspaces/`配下のrun workspaceを使用する。
- run workspaceは実行後も調査用に保持され、自動削除やTTL cleanupを行わない。
- 現在の3つのDeepEval metricが共通adapter経由でWeave scorerとして記録される。
- scorer結果に少なくとも`score`、`passed`、必要な場合は`reason`が含まれる。
- 1行のスモーク評価と、共通3行Datasetを使う正式評価のscopeが区別される。
- ハンズオンで使用するGit commitと依存関係のlockfileが固定される。
- `baseline`、`improvement-1`、`improvement-2`が同一Dataset/scorer versionで比較できる。
- `--trials`がWeaveの`Evaluation.trials`へ対応することがCLIのhelpに明記される。
- 保存済み出力の再採点が別runnerに分離され、標準ライブ評価と混同されない。
- 手動の`npm run agent`フローは引き続き利用できる。
- `tmp/.gitkeep`以外の`tmp/`配下がGitの追跡対象にならない。
- READMEで後から変更すべき内容が本書の「READMEの後続変更」に列挙されている。

## 参考資料

- [Weave Evaluations overview](https://docs.wandb.ai/weave/guides/core-types/evaluations)
- [Weave Scoring overview](https://docs.wandb.ai/weave/guides/evaluation/scorers)
- [Weave Datasets](https://docs.wandb.ai/weave/guides/core-types/datasets)
- [Weave Compare evaluations](https://docs.wandb.ai/weave/guides/evaluation/compare_evals)
- [Weave OpenRouter integration](https://docs.wandb.ai/weave/guides/integrations/openrouter)
- [DeepEval custom metrics](https://deepeval.com/docs/metrics-custom)
