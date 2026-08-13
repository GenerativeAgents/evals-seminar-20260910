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
変化した例のTraceへ戻る
```

本編では、Weave EvaluationがDatasetの各行をModelへ渡し、その場でエージェント出力を生成してscorerで採点する標準的なライブ評価を扱う。PythonとTypeScriptのプロセス境界や一時ワークスペースの管理は完成済みコードとして提供し、参加者はDataset、Model、Scorer、Evaluation、Traceの関係に集中する。

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
- Weaveのプリセットscorerと自作scorer（function-based / class-based）を使い分けられる。
- 同じDatasetとscorerを使って複数variantを比較できる。
- スコアが変化したDataset行から、関連するAgent Traceを調査できる。

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
- `improvement-1`から`improvement-2`では、主にHallucination Free（本文への忠実性）が改善する。
- SummarizationとTool Correctnessは、回帰がないことを確認する。

## 本編のアーキテクチャ

エージェント実行をEvaluationのModel呼び出しに含める。

```text
uv run eval/run_eval.py
  ├─ 自分のprojectへpublish済みのWeave Datasetをrefで取得
  ├─ SlideAgentModel.predict()を各Dataset行で呼ぶ
  ├─ TypeScriptエージェントを一時ワークスペースで実行
  ├─ Agent Traceを送信し、conversation_idを出力へ含める
  ├─ Weave Scorerで採点
  └─ Weave Evaluationへ結果を記録
```

ライブ評価によって、Dataset、Model、Scorer、Evaluationの標準的な関係と、Evaluation結果からその実行で作られたTraceへ戻る流れを一貫して体験する。

## 所要時間

本編は約40分を想定する。環境構築とAPIキー発行は事前に完了している前提とする。

| セクション | 内容                            | 目安 |
| ---------- | ------------------------------- | ---: |
| 導入       | Weaveの評価データモデル         |  3分 |
| Observe    | 事前取得したAgent Traceの確認   |  5分 |
| Dataset    | 評価対象の固定とバージョン管理  |  4分 |
| Scorer     | Weave Scorerの実装              |  8分 |
| Evaluation | 3論文のライブ評価               |  8分 |
| Compare    | 3 variantの比較                 |  7分 |
| Debug      | Evaluation行からTraceを調査     |  5分 |

## 前提条件

- Node.js v22以上
- `uv`
- OpenRouter APIキー
- W&B APIキー（参加者ごとに自分のアカウント）
- 自分のW&B entity
- リポジトリの依存関係がインストール済みであること

講師と参加者はそれぞれ別のWeaveアカウントを使い、Dataset・Evaluation・Traceはすべて各自のprojectに記録される。`.env`には自分のentityを使って次を設定する。

```env
OPENROUTER_API_KEY=...
WANDB_API_KEY=...
WEAVE_PROJECT=<your-entity>/evals-seminar-20260910
```

Weaveを主題とするため、本ハンズオンでは`WANDB_API_KEY`を必須とする。

## 0. 導入：Weaveの評価データモデル

最初に、各要素の責務を短く説明する。

| 要素       | このハンズオンでの役割                         |
| ---------- | ---------------------------------------------- |
| Trace      | エージェントのモデル・ツール・SubAgent呼び出し |
| Dataset    | 3本の論文と固定した評価用本文                  |
| Model      | variantごとのエージェント処理と追跡対象の設定  |
| Scorer     | 品質軸ごとの採点処理                           |
| Evaluation | Dataset × Model × Scorerの実行記録             |
| Compare    | baselineと改善版の集計・行単位比較             |

評価の目的は単一の総合点を作ることではなく、変更がどの品質軸とどの入力例に影響したかを確認することだと説明する。

## 1. Observe：Agent Traceを見る

### 確認

講師が自分のprojectで事前に取得したbaselineのAgent Traceを画面共有で開く。参加者自身のライブ実行はEvaluationセクションで行い、同じエージェントを本編中に重複実行しない。

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

API障害や時間超過に備え、講師が事前に取得したTrace、Evaluation、Compare画面のスクリーンショットを用意しておく。

## 2. Dataset：評価対象を固定する

参加者ごとにWeaveアカウントが異なるため、各自が3本の論文を自分のprojectへWeave Datasetとしてpublishする。行データ（`source_text`を含む）はリポジトリ内で固定されており、参加者は`publish_dataset.py`を1回実行する。

```bash
uv run eval/publish_dataset.py
```

`publish_dataset.py`の中心部分は次のとおり。

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

`run_eval.py`は、行データを毎回組み立て直すのではなく、publish済みのDatasetをrefで取得して使用する。

Weaveのオブジェクトはcontent-addressedであり、同じ内容を再publishしても新しいversionは作られない。行データはリポジトリで固定されているため、refのentity/projectは参加者ごとに異なっても、Datasetのversion（digest）は全員で一致する。

### Datasetに含めるもの

- `arxiv_id`
- `paper_url`
- 評価基準として固定した`source_text`
- 期待するツール名`expected_tools`

### Datasetに含めないもの

- `variant`
- 生成済みスライド
- scorerの結果

variantや生成結果をDatasetへ含めると、baselineと改善版で入力条件が変わり、公平な比較が難しくなる。自分のprojectでは、同じDataset version refをすべてのEvaluationで使用する。

### 学習ポイント

- Datasetは単なるPythonの配列ではなく、評価条件を固定するバージョン付き資産である。
- 同じ内容の再publishは新versionを作らない。1行でも変えるとdigestが変わり、新versionになる。
- Datasetの変更とModelの変更を区別する。
- 異なるDatasetで得た集計値は、単純に比較しない。

## 3. Scorer：品質をWeave Scorerとして定義する

### 方針

Weaveが`weave.scorers`として提供するプリセットscorerを優先して使い、プリセットにない品質軸だけを自作する。プリセットは`weave[scorers]` extraとしてインストールし、LLM-as-a-judgeを使うものはlitellm経由でjudgeを呼ぶため、`model_id`を`openrouter/<provider>/<model>`形式にするだけでOpenRouterを使える。認証は`.env`の`OPENROUTER_API_KEY`をlitellmが自動で読む。

| 品質軸             | 実装                                     |
| ------------------ | ---------------------------------------- |
| Tool Correctness   | 自作function-based scorer                |
| Summarization      | プリセット`SummarizationScorer`          |
| Hallucination Free | プリセット`HallucinationFreeScorer`      |
| Slide Quality      | 自作class-based scorer（`weave.Scorer`） |

### function-based scorer

決定的に判定できるTool Correctnessは、`@weave.op`を付けた関数として実装する。scorerの引数は、`output`がModel出力に、それ以外の引数名がDatasetの列名に対応し、Evaluationが自動で値を渡す。

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

### プリセットscorer：SummarizationとHallucination Free

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

本文への忠実性は、プリセットの`HallucinationFreeScorer`で測る。judgeが`context`と`output`を照合し、本文にない内容が含まれていないかを判定する。improvement-2で期待する変化を担う品質軸である。同じ移譲の形で組み込む。

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

スライド設計の品質（詰め込み禁止・主張型タイトル・論理的な流れ）を測るプリセットはないため、`weave.Scorer`のサブクラスとして自作する。LLM-as-a-judgeの実体は「採点基準を書いたプロンプトでLLMを1回呼ぶ」ことなので、プロンプト全文をコード上に見せる。プロンプトとjudge modelを属性として持たせると、Scorerオブジェクトごとバージョン管理され、Evaluation結果から採点条件を追跡できる。

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

scorer名は`tool_correctness`、`summarization`、`hallucination_free`、`slide_quality`のような安定した論理名とし、`v1`のような手動バージョンを付けない。Weaveは`@weave.op`のコードやScorerの属性が変わると、同じ名前の新しいバージョンを自動作成する。異なる採点基準を同じEvaluationで比較するときは、`slide_quality_balanced`と`slide_quality_strict`のように評価基準の意味を表す別名を使う。

### 学習ポイント

- scorerの引数名はDataset列と`output`に対応し、Evaluationが自動で値を渡す。
- 列名や出力形式が合わないプリセットは、`@weave.op`関数からプリセットの`score()`を呼ぶ移譲で対応付けられる。
- プリセットscorerを優先し、決定的な判定はfunction-based、プリセットにないjudge評価はclass-basedで自作する。
- scorerは数値だけでなく、判定理由なども合わせて返せる。数値は平均、boolはtrue率として自動集計される。
- scorerの論理名とWeaveが自動生成するversionを区別する。
- プロンプトやjudge modelをScorerの属性にすると、スコアの意味を決める設定がバージョン管理される。
- judge LLMのAPIエラーを0点として扱わず、Evaluation errorとして区別する。

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
)

await evaluation.evaluate(
    SlideAgentModel(variant="baseline"),
    __weave={"display_name": "baseline"},
)
```

参加者は、Dataset全体（3論文）を1 variantで評価する。反復回数のオプションは設けず、各行の実行は1回とする。

```bash
uv run eval/run_eval.py baseline
```

### 確認項目

- EvaluationのDataset ref。
- Modelのvariant。
- scorerの名前とバージョン。
- 論文ごとのスコアと理由。
- errored rowの有無。
- Model出力に含まれるconversation ID。

## 5. Compare：改善版を比較する

同じDatasetとscorerを使って3 variantを評価する。

```bash
uv run eval/run_eval.py baseline
uv run eval/run_eval.py improvement-1
uv run eval/run_eval.py improvement-2
```

参加者は`baseline`の評価までを行い、3 variantの完成結果は講師が自分のprojectで事前実行したEvaluationを画面共有で確認する。講師も同じ行データのDatasetと同じscorerコードを使うため、見方は参加者のprojectと変わらない。時間と予算に余裕がある場合は、参加者も残り2 variantを実行し、自分のprojectでCompareを開く。上記コマンド名は実装後の想定である。

WeaveのEvals画面で3つのEvaluationを選び、Compareを開く。

### 確認項目

- Slide Qualityは`improvement-1`で上がったか。
- Hallucination Freeは`improvement-2`で上がったか。
- SummarizationとTool Correctnessに回帰がないか。
- すべての論文で改善しているか、一部だけか。
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

## 実装時のファイル構成案

```text
eval/
  dataset.py             # Dataset行の組み立て（リポジトリで固定したsource_textの読み込み）
  publish_dataset.py     # 各自のprojectへDatasetをpublishするスクリプト
  scorers.py             # tool_correctness / プリセット2種への移譲wrapper / SlideQualityScorer
  agent_model.py         # SlideAgentModelとsubprocess境界
  run_eval.py            # Weave Evaluation runner

agent-run/
  runner.ts              # runSlideAgent()本体
  eval.ts                # 評価用JSON入出力エントリポイント

docs/
  weave-hands-on-plan.md
```

## 講師側の事前準備

- 自分のW&B projectを作成する。
- Dataset行データ（`source_text`）をリポジトリで固定する。
- `publish_dataset.py`を自分のprojectで実行し、Datasetのdigestを確認する。参加者のpublish結果がこのdigestと一致することが、行データが改変されていない確認になる。
- 参加者向けコマンド（3論文 × 1 variant）をスモークテストし、所要時間を実測する。
- 3 variantのライブAgent Traceを最低1件ずつ用意する。
- 同じDatasetとscorer versionで3つのライブEvaluationを事前実行し、Compare画面が表示できることを確認する。
- 各Evaluation行の`conversation_id`から対応するAgent Traceを特定できることを確認する。
- API障害時に見せるスクリーンショットまたは保存済みビューを用意する。
- エージェント生成とscorer実行の想定コスト、所要時間を確認する。

## コストと時間の調整

全参加者が3 variantすべてをライブ実行すると、エージェント生成とscorerのコスト、待ち時間が大きくなる。

本編では次の進め方を推奨する。

1. 参加者は3論文 × 1 variant（`baseline`）で実装確認する。
2. 3 variantの完成結果は講師が自分のprojectで事前実行したライブEvaluationを画面共有で確認する。時間と予算に余裕がある場合だけ、参加者も残り2 variantを実行する。
3. ライブ実行に失敗した場合も、事前取得したEvaluationとTraceでCompare、Debugまで進める。

## 完了条件

ハンズオン本編の完了条件は次のとおり。

- Agent Traceでモデル・ツール・SubAgent呼び出しを確認できた。
- 自分のprojectへpublishしたDatasetのversion refを確認できた。
- プリセットscorerと自作scorerを組み合わせて4つの品質軸を実装できた。
- `SlideAgentModel`を使い、3論文についてEvaluation内でエージェントを実行できた。
- 3 variantをCompare evaluationsで比較できた。
- 低スコアまたは回帰した行を1件説明できた。
- Evaluation行の`conversation_id`からAgent Traceを特定し、原因候補を1件以上挙げられた。

## 参考資料

- [Weave Evaluations overview](https://docs.wandb.ai/weave/guides/core-types/evaluations)
- [Weave Scoring overview](https://docs.wandb.ai/weave/guides/evaluation/scorers)
- [Weave Predefined scorers](https://docs.wandb.ai/weave/guides/evaluation/builtin_scorers)
- [Weave Datasets](https://docs.wandb.ai/weave/guides/core-types/datasets)
- [Weave Compare evaluations](https://docs.wandb.ai/weave/guides/evaluation/compare_evals)
- [Weave OpenRouter integration](https://docs.wandb.ai/weave/guides/integrations/openrouter)
