# improvement-1 / improvement-2 Weave評価比較

分析日: 2026-09-09。対象はユーザー指定の2評価のみ。評価の再実行、scorerやagentの変更、W&Bへの書き込みは行っていない。

## 結論

improvement-2は要約スコアを改善したが、事実性の改善完了とは言えない。原文にない展望、意味を狭める翻訳、評価指標の限定不足が残る。一方、ハルシネーション判定と文字数に関する採点理由にも誤判定・要再検討箇所がある。次は、既存出力で評価器を校正し、主張と原文の対応を記録・検証する工程だけを追加した対照実験を行う。

## 対象・方法

- Project: `agent-lab/evals-seminar-20260910`
- [improvement-1](https://wandb.ai/agent-lab/evals-seminar-20260910/weave/calls/01a08494-9358-7502-9bb8-f0dccdee089e): `01a08494-9358-7502-9bb8-f0dccdee089e`
- [improvement-2](https://wandb.ai/agent-lab/evals-seminar-20260910/weave/calls/01a084b0-c613-7389-b49a-4615bd3d9e7d): `01a084b0-c613-7389-b49a-4615bd3d9e7d`
- Weave SDKの`calls_query_stream`でcall IDと`Evaluation.evaluate:*`を指定し、`parent_ids`と`Evaluation.predict_and_score:*`で子評価を取得。`calls_query_stats`で子評価が合計6件であることを確認。サンプリングなし。
- 両評価の同じ3論文を対応付け、6件すべての出力、scorer結果、固定されたDataset本文を確認。対象traceの100 callsを取得して例外とscorerのop versionを照合。
- Dataset object versionは両方とも`evals-seminar-20260910:1yWJP4fFWhCgOow8jDtUNPUf0XO9zgC2b3mYsJYIUEo`。同一論文は同一row ref。
- scorerのop versionも同一。記録されたjudge設定は`openrouter/openai/gpt-5.4`。プリセットの要約・事実性scorerのtemperatureは0.7。SlideQualityScorerのローカル実装はtemperature=0。
- `conversation_id`で対応する実行時workspaceを特定し、6件すべてで保存されたskillが現在の該当variantと一致することを確認。
- 各論文1試行。以下は今回観測した差であり、再現性や統計的優位性を確定する比較ではない。

## 指標

| 指標 | improvement-1 | improvement-2 |
|---|---:|---:|
| tool_correctness.passed | 3/3 | 3/3 |
| output.generation_success | 3/3 | 3/3 |
| summarization.summarization_eval_score 平均 | 0.667 | 1.000 |
| hallucination_free.has_hallucination=true | 3/3 | 3/3 |
| SlideQualityScorer.score 平均 | 0.767 | 0.733 |
| SlideQualityScorer.passed | 3/3 | 3/3 |
| Agent処理時間平均（秒） | 160.8 | 185.9 |
| 60字を超える箇条書き（Python len） | 1/55 | 4/57 |
| 指定どおり6枚だった出力 | 2/3 | 3/3 |

`has_hallucination=true`はハルシネーション検出であり、合格ではない。`generation_success`は現行実装でスライドJSONをツールが受理した状態を表し、PPTXファイルの生成・表示品質の検証ではない。平均処理時間は`output.output.duration_ms`から算出し、judge時間を含めていない。今回の平均は15.6%増加した。

| 論文 | 要約 1→2 | スライド品質 1→2 | Agent秒数 1→2 |
|---|---:|---:|---:|
| Attention Is All You Need / 1706.03762 | 0.5 → 1.0 | 0.7 → 0.7 | 167.4 → 145.6 |
| PerplexityのAI Agent利用調査 / 2512.07828 | 0.5 → 1.0 | 0.8 → 0.8 | 163.5 → 209.3 |
| HumanLM / 2603.03303 | 1.0 → 1.0 | 0.8 → 0.7 | 151.5 → 202.9 |

ルート評価の`summary.status_counts`はそれぞれsuccess=50、error=0。これは子呼び出しを含む実行状態の集計であり、50問正解という意味ではない。rootのusageにはjudgeの一部だけが記録されているため、総生成コストの比較には使わない。

## 事実性の問題と採点の切り分け

### Attention Is All You Need

improvement-1の「その後のAI研究に革命をもたらした」という原文外の歴史的評価はimprovement-2では消えた。

しかしimprovement-2はBLEU 41.8を理由にハルシネーション扱いされている。固定Dataset本文を照合すると、要旨とTable 2は41.8、Section 6.1は41.0であり、原文内に不一致がある。41.8自体には根拠があるため、この指摘をそのまま生成側の失敗と数えるのは不適切。注記や表への参照を要求するか、source conflictとして別集計する。

一方、実際に残る内容の問題として「エンコーダ・デコーダともに…各層はMulti-Head Self-Attention + Feed-Forward Network」という記述がある。原文ではdecoderにencoder出力へのattentionを行うthird sub-layerがある。この説明はdecoderの構成を不正確に伝える。事実性scorerはimprovement-2のこの点を指摘していない。数値だけでなく、構成要素・モデル別設定・タイトルの主張も照合対象にする必要がある。

参照: [improvement-2の論文別評価](https://wandb.ai/agent-lab/evals-seminar-20260910/weave/calls/01a084b0-c61e-7fef-ab78-3b1fd139c996)

### PerplexityのAI Agent利用調査

improvement-1の「格差が普及率に直結」という因果的な言い切りは、improvement-2で相関係数を明記する表現へ改善している。

残った問題は原文の`Shopping for Goods`を「日用品ショッピング」と訳して対象を狭めている点。「物品の購入」など、原文と同じ広さの意味に修正する。数値・固有名詞が正しくても、修飾語や翻訳で主張の範囲が変わる。

参照: [improvement-2の論文別評価](https://wandb.ai/agent-lab/evals-seminar-20260910/weave/calls/01a084b3-3ef7-72a8-8b85-9617ec279d14)

### HumanLM

improvement-2の「実社会応用のための安全性検証」は原文の将来課題として裏付けられない。固定本文のFuture workは多様性とmulti-domain trainingを挙げている。これは望ましい一般論を論文の主張として補完してしまった例。

また「全データセットで最高スコア」は、どの指標かを明示していない。response alignmentの結果であることを限定し、state alignmentにも一律に適用できるような表現を避ける。

参照: [improvement-2の論文別評価](https://wandb.ai/agent-lab/evals-seminar-20260910/weave/calls/01a084b6-b678-7dca-93b5-c1dc0e57c72d)

## スライド品質と評価設計の問題

1. **情報の詰め込みが続く。** judgeは方法・結果のスライドで複数論点の同居を繰り返し指摘している。improvement-2の「主要数値と計算資源を漏らさず含める」と、6枚・1枚1メッセージという指示には緊張関係がある。これが情報量増加に寄与した可能性はあるが、今回の比較だけで因果関係は確定できない。
2. **文字数判定が不正確。** HumanLMでjudgeが60字超としたSlide 2の1項目目、Slide 4の4項目目、Slide 6の1項目目は実測54字・37字・53字。スライド品質0.8→0.7の差を、そのまま客観的劣化とは断言できない。逆に、Transformerでは69字・66字の箇条書きがあるが、理由文では極端に長くないと評価されている。形式条件はコードで判定する。
3. **指標が合否を十分に表していない。** 要約スコアはpoor/ok/excellentを0/0.5/1に写す粗い判定で、1.0は事実性保証ではない。スライド品質は閾値0.5なので今回すべてpassedになる。事実性は1箇所でも検出すればtrueになるため、重大な誤りの除去と軽微な誤訳を区別できない。
4. **ツール成功と成果物品質が混同されやすい。** tool_correctnessはexecuteとgenerate_pptxが呼ばれたかだけを見る。generate_pptxの現行実装はJSONを検証して返すもので、枚数・文字数・論文への根拠を強制しない。improvement-1のTransformerは指定6枚に対して7枚だったが合格している。PPTXの見た目やファイルの正常性はこの評価の対象外。
5. **評価器の揺らぎがある。** プリセットjudgeのtemperature=0.7、各論文1試行。モデル生成の揺らぎと採点の揺らぎを分けるため、固定出力の再採点と、同一条件の生成反復を別々に行う必要がある。

## 次の改善案

### まず既存出力で評価器を校正する

- 生成済みの同じ出力を使い、文字数・箇条書き数・スライド枚数を決定的に計測する。LLMには論点のまとまりや読みやすさを評価させる。
- Transformerの41.8/41.0をsource conflictの校正例、decoder構成を見逃し例、Perplexityの誤訳とHumanLMの展望追加を真の問題例として人手確認した判定セットを作る。
- 事実性を「根拠なし」「原文と矛盾」「原文内不一致」「範囲・翻訳の変化」に分け、重大な未支持主張件数と主張単位の支持率も残す。既存の二値指標は比較用に保持する。
- judgeの設定を固定して両variantを同じ条件で再採点する。新しい評価結果は今回の結果と別バージョンにする。

### 最優先の生成側実験: 根拠対応を必須にする

**仮説:** 「事実確認せよ」という指示だけでは、実際に照合した証拠も生成の停止条件もないため、意味の補完・過度な一般化が残る。

**変更する変数:** improvement-2の保存直前に、主張と根拠を検証・修正する工程を追加する。各タイトル・箇条書きについて、原文引用、節・表、対象タスク、指標、実験条件をsidecarへ保存する。根拠に合わない主張は修正・削除し、対応を確認してから最終JSONを確定する。引用があるだけでは意味的な支持の証明にならないため、範囲・因果・翻訳も照合する。

**固定するもの:** 同一の生成モデル設定、Dataset version、評価用本文と生成に使う本文の固定スナップショット、スライド枚数、他のプロンプト、ツール、校正済みjudge設定、反復数。比較する両群に同じ固定条件を適用する。文字数圧縮やモデル変更はこの実験へ混ぜない。

**判定:** HumanLMの根拠のない安全性検証の追加、Perplexityの「日用品」への限定、Transformerのdecoder構成の誤説明が減るか。原文内不一致は生成の誤りと区別する。反復しても未支持主張が減らなければ、指示不足という説明は弱まり、根拠検証自体の能力不足や本文抽出・参照範囲を次に調べる。

### その後の実験: 1枚で伝える主張の優先順位を決める

事実性の実験とは分離し、「主要数値を漏らさず含める」を「各スライドの主張に必要な結果を優先する」へ変更する。説明の重複を削り、詳細な設定は補足領域へ移す。要約・事実性を保ったまま複数論点の混在や実測文字数が改善するかを検証する。

## 監査用ファイル

取得・集計データは`tmp/eval-comparison-20260909/`に保存した。

- `fetch.py`: 指定2評価と全6子評価の取得
- `drill.py`: 固定Dataset本文・モデル設定・対象traceの取得
- `roots.json` / `children.json`: Weaveの評価結果
- `objects.json`: 固定されたDataset行など
- `calls.json`: 対象trace内の100 calls
- `metrics.json`: 論文別の計測結果

生成や採点を再実行するスクリプトは追加していない。
