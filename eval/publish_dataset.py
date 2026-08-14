"""リポジトリで固定した行データを自分のWeave projectへDatasetとしてpublishする。

使い方:
    uv run eval/publish_dataset.py

Weaveのオブジェクトはcontent-addressedであり、同じ内容の再publishは新しい
versionを作らない。行データはリポジトリで固定されているため、Datasetの
version(digest)は参加者全員で一致する。
"""

import weave
from dataset import DATASET_NAME, build_dataset_rows, load_settings


def main() -> None:
    settings = load_settings(require_openrouter=False)
    weave.init(settings.weave_project)

    print("[info] ar5ivから論文本文を取得しています...")
    dataset = weave.Dataset(name=DATASET_NAME, rows=build_dataset_rows())
    ref = weave.publish(dataset)

    print(f"[dataset] uri: {ref.uri()}")
    print(f"[dataset] digest: {ref.digest}")


if __name__ == "__main__":
    main()
