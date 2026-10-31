# gemini-ja-rewrite

Claude Code 用のスキルです。日本語の文章を Gemini API で自然な日本語に書き直し、意味が変わっていないかを Claude が原文と見比べて確かめます。意味のずれがあれば Gemini に差し戻し、合格するまで（最大3回）繰り返します。

手順の詳細は [SKILL.md](SKILL.md) にあります。Claude が読むための手順書です。

## 使い方

Claude Code で、対象の文章を明示して呼び出します。

```
/gemini-ja-rewrite さっきの返答をリライトして
```

`disable-model-invocation: true` にしてあるので、Claude が自分の判断でこのスキルを使うことはありません。明示的に呼んだときだけ Gemini に送られ、料金が発生します。

## セットアップ

### 1. スキルを登録する

```bash
ln -s ~/hobby/gemini-ja-rewrite ~/.claude/skills/gemini-ja-rewrite
```

### 2. API キーを置く

スクリプトは次の順にキーを探し、最初に見つかったものを使います。

1. 環境変数 `GEMINI_API_KEY`
2. 作業リポジトリの `.env` にある `GEMINI_API_KEY` の行（作業ディレクトリから Git のルートまで探す）
3. 環境変数 `GEMINI_API_KEY_CMD` で指定したコマンドの出力
4. `~/.config/gemini-ja-rewrite/api_key`

普段は 4 の共通ファイルに1つ置き、案件ごとにキーを分けたいリポジトリだけ `.env` に書く使い方を想定しています。

共通ファイルにキーを置くには、次のコマンドを実行します。キーがシェルの履歴に残らないよう、入力を求める形にしています。

```bash
mkdir -p ~/.config/gemini-ja-rewrite && (umask 077; read -rs "k?Gemini API key: " && printf '%s\n' "$k" > ~/.config/gemini-ja-rewrite/api_key)
```

上は zsh 用です。bash では `read -rsp "Gemini API key: " k` に置き換えてください。

macOS のキーチェーンや Linux の Secret Service に保存したい場合は、取り出すコマンドを `GEMINI_API_KEY_CMD` に設定します。このコマンドはシェルを通さずに実行するので、パイプは使えません。

```bash
# macOS
export GEMINI_API_KEY_CMD="security find-generic-password -s gemini-api-key -w"
# Linux（GNOME Keyring など）
export GEMINI_API_KEY_CMD="secret-tool lookup service gemini-api-key"
```

### 3. 動作を確かめる

API を呼ばずに、送る内容とキーの出どころだけを表示します。

```bash
printf 'テストの文章です。\n' > /tmp/gjr-test.md && python3 ~/.claude/skills/gemini-ja-rewrite/scripts/gemini_rewrite.py --original /tmp/gjr-test.md --dry-run
```

使えるモデルの一覧を表示します。

```bash
python3 ~/.claude/skills/gemini-ja-rewrite/scripts/gemini_rewrite.py --list-models
```

## モデルと料金

既定のモデルは `gemini-3.8-flash` です。自然さだけを任せるので Flash で足ります。変えたいときは環境変数 `GEMINI_MODEL` を設定します。

2,000字程度の文章を1回書き直す料金は、`gemini-3.8-flash` で約2.5円です（2026年11月時点の公式料金、1ドル150円で換算）。最上位の `gemini-4-argon` では約7円です。

書き直しのたびに、Gemini の応答に含まれる実際の使用量（入力、出力、思考のトークン数）から料金を計算して表示します。単価は `scripts/gemini_rewrite.py` の `PRICES` に書いてあるので、公式の料金が変わったらそこを直してください。円への換算は1ドル150円で、環境変数 `GEMINI_JPY_PER_USD` で変えられます。

無料枠で送った文章は Google の製品改善に使われます。仕事の文章を送るなら、課金を有効にした有料枠のキーを使ってください。

## テスト

```bash
python3 -m unittest discover -s tests
```
