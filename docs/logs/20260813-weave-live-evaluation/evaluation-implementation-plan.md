# Weaveを使ったライブ評価の実装方針

## 目的

現在のDeepEvalベースの評価を、W&B Weaveの`Evaluation`によるライブ評価へ置き換える。評価指標はWeaveのプリセットscorerと自作scorerで実装し、DeepEvalへの依存は削除する。ハンズオン本編では、Datasetの各行をModelへ渡し、その場でエージェント出力を生成してscorerで採点するWeaveの標準フローを扱う。

ここでいうライブ評価は、本番トラフィックとは独立した評価用Datasetに対して、その評価試行の中でエージェントを実行して採点するバッチ評価を指す。`results/<variant>/`にある過去の結果は評価入力として使用しない。

なお、エージェントによる論文取得、OpenRouterによる生成・採点、Weaveへの記録にはネットワーク接続が必要である。

## 採用する方針

- Pythonを評価のオーケストレーターとして維持し、`weave.Evaluation`を使用する。
- WeaveのModelからTypeScriptエージェントをその場で起動する。
- エージェントの出力は各run workspaceの`evaluation-result.json`から受け取り、`results/`から読み込まない。
- `workspaces/<variant>/`は実行先ではなく読み取り専用のworkspace templateとして扱い、CLI、Web UI、評価は共通の初期化処理でrun workspaceを作る。
- 品質軸は次の4つとし、Weaveのプリセットscorerを優先して使う。プリセットにない品質軸だけを自作する。
  - Tool Correctness：自作function-based scorer
  - Summarization：プリセット`SummarizationScorer`
  - Hallucination Free：プリセット`HallucinationFreeScorer`
  - Slide Quality：自作class-based scorer（`weave.Scorer`）
- DeepEval（`deepeval==4.0.7`）への依存と関連コード（`eval/cases.py`、`eval/metrics.py`）は削除する。旧metricとのスコア尺度の互換は維持しない。
- 参加者ごとにWeaveアカウントが異なるため、各自が`publish_dataset.py`でDatasetを自分のprojectへpublishする。行データはリポジトリで固定し、Datasetのversion（digest）が全員で一致するようにする。
- 自分のprojectでは全variantで同じDataset version refと同じscorerバージョンを使用し、WeaveのCompare evaluationsで比較できるようにする。
- 反復回数のオプションは設けず、各Dataset行の実行は1回とする。`--trials`のようなCLIオプションも追加しない。
- PythonとTypeScriptのプロセス境界、一時ワークスペース、JSONプロトコルは完成済みコードとして提供し、ハンズオン参加者の実装対象にはしない。
- 参加者は3論文 × 1 variant（`baseline`）をライブ評価し、3 variantの完成結果は講師が自分のprojectで事前実行したEvaluationを画面共有で確認する。時間と予算に余裕がある場合だけ、参加者も残り2 variantを実行する。

## 全体構成

```text
uv run eval/publish_dataset.py
  └─ リポジトリで固定した行データを自分のprojectへWeave Datasetとしてpublish

uv run eval/run_eval.py <variant>
  Weave Dataset（publish済みをrefで取得）
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
  slide_text / tool_calls / final_text / conversation_id
         |
         +-- tool_correctness（自作function-based）
         +-- summarization（プリセットSummarizationScorerへの移譲）
         +-- hallucination_free（プリセットHallucinationFreeScorerへの移譲）
         +-- slide_quality（自作class-based SlideQualityScorer）
```

Weave Evaluationには、Dataset、Model、scorerのバージョンと、各行の入力・出力・スコア・理由を記録する。既存のAgent Traceは同じWeave projectのAgents画面に記録し、`conversation_id`でEvaluationの行と対応付ける。

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

`source_text`はエージェントへの入力ではなく、`summarization`と`hallucination_free`のjudgeが参照する固定の評価基準として使用する。エージェント自身には`paper_url`を渡し、現在と同様に実行中に論文を取得させる。ar5ivの内容が後日変わっても過去の評価を再現できるように、取得済みの本文をDatasetのバージョンに含める。

### 参加者ごとのpublishとdigestの一致

行データ（`source_text`を含む）は`eval/dataset.py`でリポジトリ内に固定し、各参加者は`publish_dataset.py`を1回実行して自分のprojectへpublishする。

```python
dataset = weave.Dataset(
    name="evals-seminar-20260910",
    rows=build_dataset_rows(),
)

dataset_ref = weave.publish(dataset)
print(dataset_ref.uri())
```

`run_eval.py`は行データを毎回組み立て直すのではなく、publish済みのDatasetをrefで取得して使用する。

Weaveのオブジェクトはcontent-addressedであり、同じ内容を再publishしても新しいversionは作られない。行データはリポジトリで固定されているため、refのentity/projectは参加者ごとに異なっても、Datasetのversion（digest）は全員で一致する。講師は自分のprojectで事前にdigestを確認し、参加者のpublish結果がそれと一致することをもって、行データが改変されていないことを確認できる。

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

これにより、生成後に`results/`や共有されたslidesファイルを読み直す必要がなくなる。`evaluation-result.json`のキーは、Pythonのscorerが参照するModel出力（`output["slide_text"]`など）とそのまま対応するsnake_caseで統一する。

```json
{
  "generation_success": true,
  "slides": {},
  "slide_text": "...",
  "tool_calls": [],
  "final_text": "...",
  "duration_ms": 12345,
  "conversation_id": "baseline:..."
}
```

### workspace templateとrun workspaceを分離する

現在のCLIは実行ごとにagentとthread IDを作る一方、ファイルの書き込み先には固定の`workspaces/<variant>/`を使用する。Web UIもvariantごとに作成したagentが同じディレクトリを共有する。そのため、同じ論文の再実行や複数会話・複数評価行の並列実行ではファイルが競合し、過去のファイルを誤って利用する可能性がある。

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

| 実行経路         | 分離単位              |
| ---------------- | --------------------- |
| Weave Evaluation | Dataset行ごとの実行   |
| 手動CLI          | コマンド実行          |
| Web UI           | conversation / thread |

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
            variant=self.variant,
            arxiv_id=arxiv_id,
            paper_url=paper_url,
        )
```

評価対象の再現性は、ハンズオンで使用するGit commitと依存関係のlockfileを講師側で固定することで担保する。Modelのフィールドは比較軸である`variant`だけとし、外部スキルファイルのhash計算は実装しない。

プロセスの起動失敗、タイムアウト、JSONプロトコル違反はインフラエラーとして例外にし、Weave上でもその行の実行をerrorにする。

エージェントが正常終了したものの`generate_pptx`を呼ばなかった、または不正なスライドを生成した場合は、エージェントの振る舞いとして評価できるよう構造化された失敗出力を返す。scorerは、採点に必要な`slide_text`が存在しない場合、理由を付けてfailとして扱う。

## Scorer

### 方針

Weaveが`weave.scorers`として提供するプリセットscorerを優先して使い、プリセットにない品質軸だけを自作する。プリセットは`weave[scorers]` extraとしてインストールし、LLM-as-a-judgeを使うものはlitellm経由でjudgeを呼ぶため、`model_id`を`openrouter/<provider>/<model>`形式にするだけでOpenRouterを使える。認証は`.env`の`OPENROUTER_API_KEY`をlitellmが自動で読む。

| 品質軸             | 実装                                     |
| ------------------ | ---------------------------------------- |
| Tool Correctness   | 自作function-based scorer                |
| Summarization      | プリセット`SummarizationScorer`          |
| Hallucination Free | プリセット`HallucinationFreeScorer`      |
| Slide Quality      | 自作class-based scorer（`weave.Scorer`） |

scorerの引数は、`output`がModel出力に、それ以外の引数名がDatasetの列名に対応し、Evaluationが自動で値を渡す。

### function-based scorer：Tool Correctness

決定的に判定できるTool Correctnessは、`@weave.op`を付けた関数として実装する。

```python
@weave.op
def tool_correctness(output: dict, expected_tools: list[str]) -> dict:
    called = {call["name"] for call in output["tool_calls"]}
    missing = [tool for tool in expected_tools if tool not in called]
    return {
        "passed": not missing,
        "missing_tools": missing,
    }
```

### プリセットscorerへの移譲：SummarizationとHallucination Free

Summarizationにはプリセットの`SummarizationScorer`を使う。entity densityの分析とjudgeによる品質評価（poor/ok/excellent）を返す。

プリセットは原文を`input`という引数で、要約を文字列の`output`として受け取る想定なので、Datasetの列名（`source_text`）ともModel出力（dict）とも形が合わない。そこで、`@weave.op`を付けた関数からプリセットの`score()`を呼ぶ移譲の形にして、引数の対応付けと`slide_text`の取り出しを1箇所で行う。

```python
from weave.scorers import SummarizationScorer

_summarization = SummarizationScorer(model_id=JUDGE_MODEL)  # 例: "openrouter/openai/gpt-5-mini"

@weave.op
async def summarization(output: dict, source_text: str) -> dict:
    return await _summarization.score(
        input=source_text,
        output=output["slide_text"],
    )
```

本文への忠実性は、プリセットの`HallucinationFreeScorer`で測る。judgeが`context`と`output`を照合し、本文にない内容が含まれていないかを判定する。同じ移譲の形で組み込む。

```python
from weave.scorers import HallucinationFreeScorer

_hallucination_free = HallucinationFreeScorer(model_id=JUDGE_MODEL)

@weave.op
async def hallucination_free(output: dict, source_text: str) -> dict:
    return await _hallucination_free.score(
        context=source_text,
        output=output["slide_text"],
    )
```

Evaluationに登録されるscorerはwrapper関数だが、トレース上はその子callとしてプリセットの`score()`とjudge設定（`model_id`を含むオブジェクトref）が記録されるため、採点条件は追跡できる。

### class-based scorer：Slide Quality

スライド設計の品質（詰め込み禁止・主張型タイトル・論理的な流れ）を測るプリセットはないため、`weave.Scorer`のサブクラスとして自作する。プロンプトとjudge modelを属性として持たせると、Scorerオブジェクトごとバージョン管理され、Evaluation結果から採点条件を追跡できる。

```python
SLIDE_QUALITY_PROMPT = """\
あなたはプレゼン資料のレビュアーです。次のスライドを以下の基準で採点してください。

- 1枚のスライドに情報を詰め込みすぎていないか
- 各スライドのタイトルが、そのスライドの主張を一文で表しているか
- スライド全体の流れが論理的か

1（悪い）から5（良い）の整数の"score"と、判定理由の"reason"をJSONで返してください。

# スライド
{slide_text}
"""


class SlideQualityScorer(weave.Scorer):
    judge_model: str
    prompt: str = SLIDE_QUALITY_PROMPT

    @weave.op
    async def score(self, output: dict) -> dict:
        response = await litellm.acompletion(
            model=self.judge_model,
            messages=[{
                "role": "user",
                "content": self.prompt.format(slide_text=output["slide_text"]),
            }],
            response_format={"type": "json_object"},
        )
        verdict = json.loads(response.choices[0].message.content)
        return {"score": verdict["score"], "reason": verdict["reason"]}
```

### 4つのscorer

```python
scorers = [
    tool_correctness,
    summarization,
    hallucination_free,
    SlideQualityScorer(judge_model=JUDGE_MODEL),
]
```

### 命名・バージョン・エラーの扱い

scorer名は`tool_correctness`、`summarization`、`hallucination_free`、`slide_quality`のような安定した論理名とし、`v1`のような手動バージョンを付けない。Weaveは`@weave.op`のコードやScorerの属性が変わると、同じ名前の新しいバージョンを自動作成する。名前へ手動バージョンを含めると、同じscorerのバージョン履歴が別のOp系列へ分断される。異なる採点基準を同じEvaluationで並べて比較するときは、`slide_quality_balanced`と`slide_quality_strict`のように評価基準の意味を表す別名を使う。

プロンプト、judge modelなど、スコアの意味を決める値は可能な限りコードまたはScorer属性として管理し、Weaveのバージョン管理に載せる。環境変数や同じモデルIDの提供側更新など、コード変更として検出されない値には依存しない。weaveとlitellmのバージョンはlockfileで固定する。

judge LLMのAPIエラーは0点へ変換せず、そのscorer呼び出しをerrorにする。scorerは数値だけでなく判定理由なども合わせて返し、数値は平均、boolはtrue率として自動集計される。

## Evaluation runner

[`eval/run_eval.py`](../eval/run_eval.py)は次の責務を持つ。

1. `.env`を読み込む。
2. `WANDB_API_KEY`、`OPENROUTER_API_KEY`、`WEAVE_PROJECT`を検証する。`WEAVE_PROJECT`は`<your-entity>/evals-seminar-20260910`のように参加者自身のentityを使う。
3. `weave.init(WEAVE_PROJECT)`を実行する。
4. 自分のprojectへpublish済みのDatasetをrefで取得する。
5. 指定されたvariantの`SlideAgentModel`を作る。
6. 4つのscorerを構築する。
7. `weave.Evaluation`を実行する。
8. WeaveのEvaluation URL、Dataset ref、Model variant、集計結果を表示する。

概念的な実装は次の形になる。

```python
evaluation = weave.Evaluation(
    evaluation_name="evals-seminar-20260910",
    dataset=dataset,
    scorers=scorers,
)

result = await evaluation.evaluate(
    SlideAgentModel(variant=args.variant),
    __weave={"display_name": args.variant},
)
```

参加者は、Dataset全体（3論文）を1 variantで評価する。

```bash
uv run eval/run_eval.py baseline
```

講師は同じコマンドで3 variantを自分のprojectで事前実行し、Compare evaluationsを画面共有で確認できるようにする。講師も同じ行データのDatasetと同じscorerコードを使うため、digestとscorer versionは参加者のprojectと一致する。

```bash
uv run eval/run_eval.py baseline
uv run eval/run_eval.py improvement-1
uv run eval/run_eval.py improvement-2
```

Weaveを評価結果の正本とする。ローカルJSONが必要な場合は、Evaluation完了後にWeave Evaluation APIからエクスポートする機能を別途追加し、評価入力としては使用しない。

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
  eval.ts                # 評価用JSON入出力エントリポイントを新設

agent/
  run-workspace.ts       # templateからrun workspaceを作成する共通処理

app/api/copilotkit/
  route.ts               # conversation単位のagent/backend/workspace管理へ変更

eval/
  dataset.py             # Dataset行の組み立て（リポジトリで固定したsource_textの読み込み）
  publish_dataset.py     # 各自のprojectへDatasetをpublishするスクリプト
  scorers.py             # tool_correctness / プリセット2種への移譲wrapper / SlideQualityScorer
  agent_model.py         # SlideAgentModelとsubprocess境界を新設
  run_eval.py            # weave.Evaluationベースへ変更
  cases.py               # 削除（DeepEval用のLLMTestCase組み立て）
  metrics.py             # 削除（DeepEval metricの構築）

pyproject.toml           # weave[scorers]とlitellmを追加、deepevalを削除

tmp/
  .gitkeep               # run workspace親ディレクトリだけをGit管理

docs/logs/20260813-weave-live-evaluation/
  hands-on-plan.md       # ライブEvaluation中心の教材構成
```

ファイル名は実装時に既存構成との整合性を見て調整してよいが、TypeScriptのエージェント実行、PythonのWeave Model、scorerの責務は分離する。

## 実装順序

1. workspace templateからrun workspaceを作成する共通処理を実装する。
2. `runSlideAgent()`を切り出し、CLIをコマンド実行単位のrun workspaceへ移行しながら現在の手動CLIの動作を維持する。
3. Web UIをconversation単位のagent/backend/run workspace管理へ移行する。
4. 評価用JSONプロトコルを実装する。
5. Pythonからエージェントを1回起動する`SlideAgentModel`を実装する。
6. `dataset.py`と`publish_dataset.py`を実装し、自分のprojectでdigestを確認する。
7. `scorers.py`に4つのscorerを実装する。
8. `run_eval.py`を`weave.Evaluation`へ移行し、DeepEval関連コードと依存を削除する。
9. 3論文 × 1 variant（`baseline`）でスモークテストし、所要時間を実測する。
10. 3論文 × 3 variantを同じDataset/scorer versionで実行し、Compare evaluationsを確認する。
11. Evaluation行の`conversation_id`からAgent Traceへ移動できることを確認する。
12. 必要に応じてWeave Evaluation APIからローカルJSONへエクスポートする機能を追加する。

## READMEの後続変更

READMEは本方針の変更時点では編集せず、ライブEvaluation実装とコマンドが確定した後に別作業として統一する。後続のREADME変更には少なくとも次を含める。

- 「評価は`results/<variant>/`だけを読む」という説明を削除する。
- DeepEvalに関する記載を削除し、Weaveプリセットscorerと自作scorerによる4つの品質軸へ置き換える。
- `publish_dataset.py`で自分のprojectへDatasetをpublishする手順と、`.env`の`WEAVE_PROJECT`に自分のentityを設定することを記載する。
- 標準の評価コマンドを`run_eval.py`のライブ評価（3論文 × 1 variant）へ変更する。
- 反復回数のオプションはないこと（各行の実行は1回）を記載する。
- `results/`は手動実行の成果物であり、ライブ評価の入力ではないことを明記する。
- WeaveをEvaluation結果の正本とし、ローカルJSONが必要な場合はEvaluation完了後にエクスポートする方針を記載する。
- Evaluation行の`conversation_id`からAgent Traceを調査する手順を追加する。
- `workspaces/<variant>/`はworkspace templateであり、CLI、Web UI、評価の実行時には`tmp/workspaces/`配下のrun workspaceを使用することを説明する。
- CLIはコマンド単位、Web UIはconversation単位、EvaluationはDataset行の実行単位でworkspaceを分離することを説明する。
- `tmp/`配下のrun workspaceは実行後も調査用に残り、自動削除しないことを説明する。
- `agent/run-workspace.ts`、`agent-run/runner.ts`、`agent-run/eval.ts`、`app/api/copilotkit/route.ts`、`eval/dataset.py`、`eval/publish_dataset.py`、`eval/scorers.py`、`eval/agent_model.py`を反映してファイル構成を更新する。

## テスト方針

### 単体テスト

- `tool_correctness`が呼び出し済みツールと`expected_tools`から`passed`と`missing_tools`を正しく作ること。
- `summarization`と`hallucination_free`のwrapperが、Datasetの`source_text`とModel出力の`slide_text`をプリセットの`input`/`context`と`output`へ正しく対応付けること。
- `SlideQualityScorer`がプロンプトとjudge modelを属性として保持し、judgeの返すJSONから`score`と`reason`を取り出すこと。
- scorerの論理名に手動バージョンを含めず、コード変更が同じOp系列の新しいWeave versionとして記録されること。
- judge LLMのAPIエラーを0点へ変換せず、scorer errorとして伝播すること。
- 評価用エントリポイントが指定run IDのworkspaceへ`evaluation-result.json`を書き、標準出力のログと混ざらないこと。
- run workspace名が`<yyyyMMddHHmmss>-<variant>-<run-id>`形式であり、同じ秒・同じvariantでも衝突しないこと。
- templateから必要な設定だけをコピーし、既存の`slides/`と`large_tool_results/`を引き継がないこと。
- CLIの各実行とEvaluationの各行実行が異なるrun workspaceを使用すること。
- Web UIではconversation間でrun workspaceが分離され、同じconversationの複数ターンでは再利用されること。
- CLI、Evaluation、Web UIのrun workspaceが実行後も保持されること。
- 古いslidesファイルや`results/`が評価結果に混入しないこと。
- 同じ行データの再publishでDatasetの新versionが作られないこと。

judge LLMを呼ばないテストでは、litellmとプリセットscorerの呼び出しをfakeへ差し替える。

### 結合テスト

- 1論文についてライブエージェントが起動されること。
- `generate_pptx`の検証済み出力がModel出力になること。
- Evaluationに4つのscorer列と理由が記録されること。
- Model出力の`conversation_id`からAgent Traceを特定できること。
- 同じDataset refとscorer versionでvariant間比較ができること。

## 完了条件

- 評価実行中に各Dataset行でエージェントが新規実行される。
- `run_eval.py`は`results/<variant>/`を評価入力として読み込まない。
- 並列実行間でワークスペースや生成ファイルが共有されない。
- `workspaces/<variant>/`が読み取り専用のworkspace templateとして扱われ、CLI、Web UI、評価から変更されない。
- CLIは実行単位、Web UIはconversation単位、EvaluationはDataset行の実行単位で`tmp/workspaces/`配下のrun workspaceを使用する。
- run workspaceは実行後も調査用に保持され、自動削除やTTL cleanupを行わない。
- 4つの品質軸がプリセットscorerと自作scorerの組み合わせで実装され、Weave上でバージョン付きで記録される。
- scorer結果に少なくともスコア（数値またはbool）と、judge評価では`reason`が含まれる。
- DeepEvalへの依存（`pyproject.toml`、`eval/cases.py`、`eval/metrics.py`）が削除されている。
- `publish_dataset.py`によるDatasetのdigestが、講師の事前確認したdigestと一致する。
- `baseline`、`improvement-1`、`improvement-2`が同一Dataset/scorer versionで比較できる。
- 反復回数のオプションが存在せず、各Dataset行の実行が1回である。
- 手動の`npm run agent`フローは引き続き利用できる。
- `tmp/.gitkeep`以外の`tmp/`配下がGitの追跡対象にならない。
- READMEで後から変更すべき内容が本書の「READMEの後続変更」に列挙されている。

## 参考資料

- [Weave Evaluations overview](https://docs.wandb.ai/weave/guides/core-types/evaluations)
- [Weave Scoring overview](https://docs.wandb.ai/weave/guides/evaluation/scorers)
- [Weave Predefined scorers](https://docs.wandb.ai/weave/guides/evaluation/builtin_scorers)
- [Weave Datasets](https://docs.wandb.ai/weave/guides/core-types/datasets)
- [Weave Compare evaluations](https://docs.wandb.ai/weave/guides/evaluation/compare_evals)
- [Weave OpenRouter integration](https://docs.wandb.ai/weave/guides/integrations/openrouter)
