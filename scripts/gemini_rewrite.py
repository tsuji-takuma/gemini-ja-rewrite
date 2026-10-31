#!/usr/bin/env python3
"""Gemini API に日本語の書き直しを1回だけ依頼する補助スクリプト。

繰り返しの管理と意味の検証は Claude が SKILL.md の手順で行う。
このスクリプトは「依頼文を受け取り、書き直した文を返す」だけを受け持つ。

標準ライブラリだけで書き、Python 3.9 以降の macOS と Linux で動くようにしている。
"""

import argparse
import json
import os
import re
import shlex
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# 自然さだけを任せるので Flash で足りる。意味の検証は Claude が受け持つ
DEFAULT_MODEL = "gemini-3.8-flash"
KEY_NAME = "GEMINI_API_KEY"
REQUEST_TIMEOUT_SEC = 300

SKILL_DIR = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT_PATH = SKILL_DIR / "prompts" / "gemini.md"

# 終了コード。SKILL.md の手順がこれを見て次の動きを決める。
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_API = 2          # キーがない、API エラー、ブロックなど。Claude はそこで止まる
EXIT_PLACEHOLDER = 3  # 保護した部分が書き直し後に揃っていない。1回分の失敗として扱う


class RewriteError(Exception):
    pass


# --- 保護する部分の置き換え ---------------------------------------------
#
# コードと URL は Gemini に触らせたくないので、送る前に目印に置き換え、
# 返ってきた文で元に戻す。指示で守らせるより確実なため。

PLACEHOLDER_FMT = "{{{{KEEP{}}}}}"
PLACEHOLDER_RE = re.compile(r"\{\{KEEP(\d+)\}\}")

PROTECT_PATTERNS = [
    re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL),
    re.compile(r"`[^`\n]+`"),
    # URL は ASCII だけで終わるものとして扱い、直後の日本語や句読点を巻き込まない
    re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%]*[A-Za-z0-9\-_~/#@$&*+=%]"),
]


def mask(text: str) -> Tuple[str, Dict[str, str]]:
    mapping: Dict[str, str] = {}
    by_segment: Dict[str, str] = {}

    def replace(match: "re.Match[str]") -> str:
        segment = match.group(0)
        if segment not in by_segment:
            token = PLACEHOLDER_FMT.format(len(by_segment) + 1)
            by_segment[segment] = token
            mapping[token] = segment
        return by_segment[segment]

    for pattern in PROTECT_PATTERNS:
        text = pattern.sub(replace, text)
    return text, mapping


def apply_mask(text: str, mapping: Dict[str, str]) -> str:
    """前回の書き直しにも、原文と同じ目印を当てる。"""
    for token, segment in sorted(mapping.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(segment, token)
    return text


def unmask(text: str, mapping: Dict[str, str]) -> Tuple[str, List[str], List[str]]:
    """目印を元に戻し、消えた目印と増えた目印を返す。"""
    found = PLACEHOLDER_RE.findall(text)
    found_tokens = [PLACEHOLDER_FMT.format(n) for n in found]
    missing = [t for t in mapping if t not in found_tokens]
    duplicated = sorted({t for t in found_tokens if found_tokens.count(t) > 1})
    unknown = sorted({t for t in found_tokens if t not in mapping})

    def restore(match: "re.Match[str]") -> str:
        return mapping.get(match.group(0), match.group(0))

    restored = PLACEHOLDER_RE.sub(restore, text)
    return restored, missing, duplicated + unknown


# --- API キーの探索 -----------------------------------------------------


def parse_env_file(path: Path, name: str = KEY_NAME) -> Optional[str]:
    """.env から指定の1行だけを読む。ほかの変数は環境に読み込まない。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        return value or None
    return None


def find_git_root(start: Path) -> Optional[Path]:
    for directory in [start, *start.parents]:
        if (directory / ".git").exists():
            return directory
    return None


def find_env_file(start: Path) -> Optional[Path]:
    """作業ディレクトリから上へ .env を探し、Git のルートで止める。

    Git の外ではホームディレクトリなどの無関係な .env を拾わないよう、
    作業ディレクトリだけを見る。
    """
    start = start.resolve()
    root = find_git_root(start)
    directories = [start] if root is None else [start, *start.parents][: len(start.parts) - len(root.parts) + 1]
    for directory in directories:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def env_file_is_tracked(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path.name],
            cwd=str(path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def config_key_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gemini-ja-rewrite" / "api_key"


def find_api_key(cwd: Path, warn=lambda msg: None) -> Tuple[Optional[str], str]:
    """キーと、その出どころの名前を返す。キーの値はどこにも表示しない。"""
    value = os.environ.get(KEY_NAME, "").strip()
    if value:
        return value, f"環境変数 {KEY_NAME}"

    env_file = find_env_file(cwd)
    if env_file:
        value = parse_env_file(env_file)
        if value:
            if env_file_is_tracked(env_file):
                warn(f"警告: {env_file} が Git の管理対象です。キーがコミットされるおそれがあります。")
            return value, f"{env_file}"

    command = os.environ.get("GEMINI_API_KEY_CMD", "").strip()
    if command:
        try:
            result = subprocess.run(
                shlex.split(command),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            result = None
        if result is not None and result.returncode == 0:
            value = result.stdout.decode("utf-8", "replace").strip()
            if value:
                return value, "GEMINI_API_KEY_CMD"
        warn("警告: GEMINI_API_KEY_CMD を実行しましたが、キーを取り出せませんでした。")

    key_file = config_key_path()
    if key_file.is_file():
        mode = stat.S_IMODE(key_file.stat().st_mode)
        if mode & 0o077:
            warn(f"警告: {key_file} の権限が {oct(mode)} です。chmod 600 を推奨します。")
        try:
            value = key_file.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
            warn(f"警告: {key_file} を読めませんでした。")
        if value:
            return value, str(key_file)

    return None, ""


def key_not_found_message() -> str:
    return (
        "Gemini の API キーが見つかりません。次の順に探しました。\n"
        f"  1. 環境変数 {KEY_NAME}\n"
        f"  2. 作業ディレクトリから Git のルートまでの .env にある {KEY_NAME}\n"
        "  3. 環境変数 GEMINI_API_KEY_CMD で指定したコマンドの出力\n"
        f"  4. {config_key_path()}\n"
        "設定方法はスキルの README.md を参照してください。"
    )


# --- 依頼文の組み立て ---------------------------------------------------


def build_user_prompt(
    original: str,
    intent: Optional[str] = None,
    note: Optional[str] = None,
    previous: Optional[str] = None,
    feedback: Optional[str] = None,
) -> str:
    parts = ["## 原文", original.strip()]
    if intent and intent.strip():
        parts += ["## 書き手の意図", intent.strip()]
    if note and note.strip():
        parts += ["## 補足の指示", note.strip()]
    if previous is not None:
        parts += [
            "## 前回の書き直し",
            previous.strip(),
            "## 検証で見つかった意味のずれ",
            (feedback or "").strip(),
            "## 今回の依頼",
            "前回の書き直しを土台にして、指摘された箇所だけを原文の意味に合うように直してください。"
            "指摘のない部分は変えないでください。",
        ]
    else:
        parts += ["## 依頼", "原文を、意味を変えずに自然な日本語に書き直してください。"]
    return "\n\n".join(parts) + "\n"


def build_request_body(system_prompt: str, user_prompt: str) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
    }


# --- API 呼び出し -------------------------------------------------------


def http_json(method: str, url: str, api_key: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    # キーは URL に載せず、ヘッダーで送る
    request.add_header("x-goog-api-key", api_key)
    if data is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = detail[:500]
        raise RewriteError(f"Gemini API がエラーを返しました（HTTP {err.code}）: {message}")
    except urllib.error.URLError as err:
        raise RewriteError(f"Gemini API に接続できませんでした: {err.reason}")
    except (TimeoutError, socket.timeout):
        raise RewriteError("Gemini API の応答がタイムアウトしました。")


def extract_text(response: dict) -> Tuple[str, Optional[str]]:
    """応答から本文を取り出す。思考の部分は除く。終了理由も返す。"""
    block = (response.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise RewriteError(f"Gemini が依頼を受け付けませんでした（blockReason: {block}）。")
    candidates = response.get("candidates") or []
    if not candidates:
        raise RewriteError("Gemini の応答に書き直した文が含まれていませんでした。")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise RewriteError(f"Gemini の応答が空でした（finishReason: {finish}）。")
    return text, finish


def strip_wrapping_fence(text: str) -> str:
    """全体がコードフェンスで囲まれて返ってきたときだけ外す。"""
    match = re.fullmatch(r"\s*```[a-zA-Z]*\n(.*)\n```\s*", text, re.DOTALL)
    return match.group(1) if match else text


# --- コマンド -----------------------------------------------------------


def read_optional(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def cmd_rewrite(args: argparse.Namespace) -> int:
    def warn(msg: str) -> None:
        print(msg, file=sys.stderr)

    original = Path(args.original).read_text(encoding="utf-8")
    if not original.strip():
        warn("原文が空です。")
        return EXIT_USAGE
    if (args.previous is None) != (args.feedback is None):
        warn("--previous と --feedback は一緒に指定してください。")
        return EXIT_USAGE

    masked_original, mapping = mask(original)
    previous = read_optional(args.previous)
    masked_previous = apply_mask(previous, mapping) if previous is not None else None

    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = build_user_prompt(
        masked_original,
        intent=read_optional(args.intent),
        note=read_optional(args.note),
        previous=masked_previous,
        feedback=read_optional(args.feedback),
    )
    model = args.model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL

    api_key, source = find_api_key(Path.cwd(), warn=warn)

    if args.dry_run:
        print(f"[dry-run] model: {model}")
        print(f"[dry-run] API キー: {source or '見つかりません'}")
        print(f"[dry-run] 保護した部分: {len(mapping)} 個")
        print("[dry-run] ----- system instruction -----")
        print(system_prompt)
        print("[dry-run] ----- user prompt -----")
        print(user_prompt)
        return EXIT_OK

    if not api_key:
        warn(key_not_found_message())
        return EXIT_API

    url = f"{API_BASE}/models/{model}:generateContent"
    try:
        response = http_json("POST", url, api_key, build_request_body(system_prompt, user_prompt))
        text, finish = extract_text(response)
    except RewriteError as err:
        warn(str(err))
        return EXIT_API

    text = strip_wrapping_fence(text).strip() + "\n"
    restored, missing, extra = unmask(text, mapping)

    if args.out:
        Path(args.out).write_text(restored, encoding="utf-8")
    else:
        sys.stdout.write(restored)

    print(f"model: {model} / API キー: {source} / finishReason: {finish}", file=sys.stderr)
    if finish and finish != "STOP":
        warn(f"警告: 応答が途中で終わった可能性があります（finishReason: {finish}）。")
    if missing or extra:
        problems = []
        if missing:
            problems.append("消えた部分: " + " / ".join(mapping[t] for t in missing))
        if extra:
            problems.append("重複または不明な目印: " + ", ".join(extra))
        warn("保護したコードや URL が書き直し後に揃っていません。" + "; ".join(problems))
        return EXIT_PLACEHOLDER
    return EXIT_OK


def cmd_list_models(args: argparse.Namespace) -> int:
    def warn(msg: str) -> None:
        print(msg, file=sys.stderr)

    api_key, _ = find_api_key(Path.cwd(), warn=warn)
    if not api_key:
        warn(key_not_found_message())
        return EXIT_API
    page_token = ""
    while True:
        url = f"{API_BASE}/models?pageSize=1000"
        if page_token:
            url += f"&pageToken={page_token}"
        try:
            data = http_json("GET", url, api_key)
        except RewriteError as err:
            warn(str(err))
            return EXIT_API
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            name = m.get("name", "").replace("models/", "", 1)
            print(f"{name}\t{m.get('displayName', '')}")
        page_token = data.get("nextPageToken", "")
        if not page_token:
            return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini API に日本語の書き直しを1回依頼する。")
    parser.add_argument("--original", help="原文のファイル")
    parser.add_argument("--intent", help="書き手の意図を書いたファイル（任意）")
    parser.add_argument("--note", help="利用者からの補足の指示を書いたファイル（任意）")
    parser.add_argument("--previous", help="前回の書き直しのファイル（差し戻し時）")
    parser.add_argument("--feedback", help="意味のずれの指摘を書いたファイル（差し戻し時）")
    parser.add_argument("--out", help="書き直した文の保存先。省略すると標準出力")
    parser.add_argument("--model", help=f"使うモデル。既定は GEMINI_MODEL か {DEFAULT_MODEL}")
    parser.add_argument("--dry-run", action="store_true", help="API を呼ばず、送る内容だけを表示する")
    parser.add_argument("--list-models", action="store_true", help="使えるモデルの一覧を表示する")
    args = parser.parse_args(argv)

    if args.list_models:
        return cmd_list_models(args)
    if not args.original:
        parser.error("--original を指定してください。")
    return cmd_rewrite(args)


if __name__ == "__main__":
    sys.exit(main())
