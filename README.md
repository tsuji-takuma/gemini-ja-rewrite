# gemini-ja-rewrite

AI が書いた日本語を Gemini API で自然な文章に書き直し、意味が変わっていないかを Claude が検証する Claude Code 用のスキルです。

## 仕組み

AI が生成した日本語には、直訳調の言い回しや硬い名詞の連続など、読み手が違和感を覚える不自然さが残りがちです。一方で、別のモデルに書き直させると、文章は自然になっても意味が少しずつ変わってしまうことがあります。

本スキルでは役割を明確に分担し、自然さと正確さの両立を図っています。

- **Gemini**：日本語として自然で読みやすい言い回しに書き直します。
- **Claude**：書き直した文章を原文と突き合わせ、主張、条件、数値、否定、確信度などが損なわれていないかを検証します。

意味の変化が見つかった場合、Claude はその箇所だけを具体的に指摘して Gemini に差し戻します。検証を通過するまで最大3回やり取りを重ね、最終的に合格した版を出力します。

```
原文 ─→ Gemini が書き直す ─→ Claude が原文と見比べる ─→ 合格なら提示
              ↑                          │
              └──── 変わった箇所を指摘 ←───┘（最大3回）
```

詳しい処理の流れは、Claude 向けの手順書 [SKILL.md](SKILL.md) を参照してください。

## 必要な環境

- Claude Code
- Python 3.9 以上（標準ライブラリのみで動作するため、追加パッケージのインストールは不要です）
- Gemini API キー（取得方法は [Gemini API のドキュメント](https://ai.google.dev/gemini-api/docs/api-key) を参照してください）

## インストール

### 1. スキルの配置

Claude Code のスキル用ディレクトリにリポジトリをクローンします。

```bash
git clone https://github.com/tsuji-takuma/gemini-ja-rewrite.git ~/.claude/skills/gemini-ja-rewrite
```

別の場所で管理したい場合は、クローン先ディレクトリへのシンボリックリンクを `~/.claude/skills/gemini-ja-rewrite` に作成してください。`/gemini-ja-rewrite` が認識されないときは、Claude Code を再起動してください。

### 2. API キーの設定

もっとも手軽なのは、`~/.config/gemini-ja-rewrite/api_key` に保存する方法です。以下のコマンドを実行するとプロンプト経由で入力できるため、キーがシェルの履歴に残りません（zsh 用）。

```bash
mkdir -p ~/.config/gemini-ja-rewrite && (umask 077; read -rs "k?Gemini API key: " && printf '%s\n' "$k" > ~/.config/gemini-ja-rewrite/api_key)
```

bash をお使いの場合は、`read -rs "k?Gemini API key: "` を `read -rsp "Gemini API key: " k` に置き換えてください。その他の設定方法については「設定」セクションを参照してください。

### 3. 動作確認

API は呼び出さず、送信予定の内容とキーの取得元のみを確認します。料金は発生しません。

```bash
printf 'テストの文章です。\n' > /tmp/gjr-test.md && python3 ~/.claude/skills/gemini-ja-rewrite/scripts/gemini_rewrite.py --original /tmp/gjr-test.md --dry-run
```

## 使い方

Claude Code 上で、書き直したい文章を指定してスキルを呼び出します。

```
/gemini-ja-rewrite さっきの返答をリライトして
```

```
/gemini-ja-rewrite README.md をリポジトリの README として適切な文体で書き直して
```

対象とする文章には、Claude が会話内で生成したテキストのほか、直接貼り付けた文章やファイル内のテキストも指定できます。「リポジトリの README として適切な文体で」といった補足指示を添えると、そのまま Gemini に渡されます。

検証に合格すると、出力された文章の末尾に次のような情報が添えられます。

```
Gemini（gemini-3.8-flash）の書き直しを2回目で採用しました。料金は2回分で約2.1円（0.0140 ドル）でした。
```

なお、このスキルは明示的に呼び出したときのみ動作します（`disable-model-invocation: true`）。Claude が自身の判断で勝手に Gemini を呼び出し、意図しない料金が発生することはありません。

## 設定

| 環境変数 | 内容 | 既定値 |
|---|---|---|
| `GEMINI_API_KEY` | API キー | なし |
| `GEMINI_API_KEY_CMD` | API キーを取り出すコマンド（シェルを経由せず直接実行するため、パイプは使用できません） | なし |
| `GEMINI_MODEL` | 使用するモデル | `gemini-3.8-flash` |
| `GEMINI_JPY_PER_USD` | 料金を日本円に換算する際の為替レート | `150` |

API キーは次の優先順位で探索され、最初に見つかったものが使用されます。

1. 環境変数 `GEMINI_API_KEY`
2. 作業中リポジトリの `.env` に設定された `GEMINI_API_KEY`（作業ディレクトリから Git のルートまで探索）
3. `GEMINI_API_KEY_CMD` の出力結果
4. `~/.config/gemini-ja-rewrite/api_key`

通常は 4 に1つ設定しておき、プロジェクトごとにキーを使い分けたいリポジトリでのみ `.env` を利用する形を想定しています。`.env` の書き方については [.env.example](.env.example) を参照してください。なお、`.env` から読み込まれるのは `GEMINI_API_KEY` のみです。

macOS のキーチェーンや Linux の Secret Service に保存したキーを利用する例です。

```bash
# macOS
export GEMINI_API_KEY_CMD="security find-generic-password -s gemini-api-key -w"
# Linux（GNOME Keyring など）
export GEMINI_API_KEY_CMD="secret-tool lookup service gemini-api-key"
```

## 料金とデータの取り扱い

- 書き直しの対象となる文章は Google のサーバーに送信されます。無料枠のキーで送信された文章は、Google の製品改善に利用されます。業務上の文章を扱う場合は、課金設定を有効にした有料枠のキーを使用してください。
- 実行ごとに、実際のトークン消費量に基づいて計算された料金が表示されます。
- 目安として、`gemini-3.8-flash` を使用して約1万字の文章を1回書き直した場合の料金は、5〜7円程度でした（2026年11月計測）。差し戻しが発生した場合は原文と前回の出力内容の双方が送信されるため、2回目以降の処理費用は1回目よりも高くなります。
- 最新の料金単価については、[Gemini API の料金ページ](https://ai.google.dev/gemini-api/docs/pricing) を確認してください。

## 開発

テストを実行します。API の呼び出しは行いません。

```bash
python3 -m unittest discover -s tests
```

ファイル構成は次のとおりです。

- `SKILL.md`：Claude 向けの手順書
- `prompts/gemini.md`：Gemini へのシステム指示
- `scripts/gemini_rewrite.py`：Gemini に1回分の書き直しをリクエストするスクリプト
- `tests/`：単体テストコード

料金計算に使用する単価は `scripts/gemini_rewrite.py` 内の `PRICES` で定義されています。公式の価格改定があった場合は、こちらの値を更新してください。
