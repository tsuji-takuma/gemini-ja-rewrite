# gemini-ja-rewrite

Claude Code 用のスキルです。Gemini API を使って日本語の文章を自然な表現へと書き直し、原文から意味が変わっていないかを Claude が突き合わせて検証します。意味にずれがあれば Gemini に差し戻し、基準を満たすまで（最大3回）推敲を繰り返します。

詳しい処理の流れは、Claude 用の手順書である [SKILL.md](SKILL.md) をご覧ください。

## 使い方

Claude Code から、対象の文章を指定して呼び出します。

```
/gemini-ja-rewrite さっきの返答をリライトして
```

`disable-model-invocation: true` に設定されているため、Claude が独断でこのスキルを実行することはありません。明示的に呼び出したときにのみ Gemini へ送信され、料金が発生します。

## セットアップ

### 1. スキルの登録

```bash
ln -s ~/hobby/gemini-ja-rewrite ~/.claude/skills/gemini-ja-rewrite
```

### 2. API キーの設定

スクリプトは次の優先順位でキーを探索し、最初に見つかったものを使用します。

1. 環境変数 `GEMINI_API_KEY`
2. 作業リポジトリ内の `.env` にある `GEMINI_API_KEY` の記述（カレントディレクトリから Git ルートまで探索）
3. 環境変数 `GEMINI_API_KEY_CMD` で指定されたコマンドの出力結果
4. `~/.config/gemini-ja-rewrite/api_key`

普段は 4 の共通ファイルにキーを1つ置いておき、プロジェクトごとにキーを分けたいリポジトリでのみ `.env` を使う運用を想定しています。`.env` を使う場合は、[.env.example](.env.example) をコピーしてキーを記入してください。

共通ファイルにキーを保存するには、次のコマンドを実行します。キーがシェルの実行履歴に残らないよう、対話式で入力を受け付ける構成にしています。

```bash
mkdir -p ~/.config/gemini-ja-rewrite && (umask 077; read -rs "k?Gemini API key: " && printf '%s\n' "$k" > ~/.config/gemini-ja-rewrite/api_key)
```

上記は zsh 用です。bash をお使いの場合は、`read -rsp "Gemini API key: " k` に置き換えて実行してください。

macOS のキーチェーンや Linux の Secret Service にキーを保存したい場合は、値を取り出すコマンドを `GEMINI_API_KEY_CMD` に指定します。このコマンドはシェルを介さず直接実行されるため、パイプは使用できません。

```bash
# macOS
export GEMINI_API_KEY_CMD="security find-generic-password -s gemini-api-key -w"
# Linux（GNOME Keyring など）
export GEMINI_API_KEY_CMD="secret-tool lookup service gemini-api-key"
```

### 3. 動作確認

API を呼び出さずに、送信内容と API キーの参照元のみを出力します。

```bash
printf 'テストの文章です。\n' > /tmp/gjr-test.md && python3 ~/.claude/skills/gemini-ja-rewrite/scripts/gemini_rewrite.py --original /tmp/gjr-test.md --dry-run
```

利用可能なモデルの一覧を表示します。

```bash
python3 ~/.claude/skills/gemini-ja-rewrite/scripts/gemini_rewrite.py --list-models
```

## モデルと料金

デフォルトのモデルは `gemini-3.8-flash` です。文章の自然さを整える用途であれば Flash で十分に対応できます。モデルを変更したい場合は、環境変数 `GEMINI_MODEL` を指定してください。

2,000字程度の文章を1回書き直す場合の料金は、`gemini-3.8-flash` で約2.5円です（2026年11月時点の公式料金、1ドル＝150円換算）。最上位モデルの `gemini-4-argon` を使用した場合は約7円となります。

推敲を行うたびに、Gemini のレスポンスに含まれる実際の使用量（入力・出力・思考の各トークン数）に基づいて料金を算出して表示します。計算用の単価は `scripts/gemini_rewrite.py` 内の `PRICES` に定義されているため、公式料金の改定があった際はこちらを更新してください。日本円への換算レートは 1ドル＝150円 を基準としており、環境変数 `GEMINI_JPY_PER_USD` で変更可能です。

無料枠のキーで送信された文章は、Google の製品改善データとして利用されます。業務関連の文章を扱う際は、課金を有効化した有料枠のキーを使用してください。

## テスト

```bash
python3 -m unittest discover -s tests
```
