#!/bin/bash
# DCL スタンプラリー 日次更新（ローカル）。ランキング日次routineの後に走らせる。
#   1. gen_tasksheet.py … 課題申告シートの名簿マージ更新（申告セル温存・卒業/対象外フラグ・変化時のみ書込）
#   2. gen.py           … ライバー別JSONを再生成（成果=クリエイターデータ_Claude / 課題=課題申告シート）
#   3. 変更があれば git push → GitHub Actions(deploy.yml)が本番Pagesへ反映
#   診断ログは tools/daily.log。
set -uo pipefail
REPO="$HOME/Claude/stamp-rally"
PY="$HOME/Claude/pococha/.venv/bin/python"
cd "$REPO" || { echo "❌ stamp-rally が見つかりません"; exit 1; }
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/node/bin:$PATH"

LOG="$REPO/tools/daily.log"
say(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

LOCK="$REPO/tools/.daily.lock"
if ! mkdir "$LOCK" 2>/dev/null; then say "既に実行中のため中止"; exit 0; fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

say "===== 開始 ====="

# 1. 名簿マージ更新（変化が無ければ内部でスキップ）
if ! "$PY" tools/gen_tasksheet.py >>"$LOG" 2>&1; then
  say "❌ gen_tasksheet 失敗"; exit 1
fi

# 2. JSON再生成
if ! "$PY" tools/gen.py >>"$LOG" 2>&1; then
  say "❌ gen 失敗"; exit 1
fi

# 3. 変更があれば push
if ! git diff --quiet || ! git diff --cached --quiet; then
  git add -A
  git -c user.name="dcl-events" -c user.email="noreply@dena.com" commit -q -m "chore: update rally data (local daily)"
  if git push -q 2>>"$LOG"; then
    say "✅ push 完了"
  else
    say "❌ push 失敗"; exit 1
  fi
else
  say "変更なし（pushスキップ）"
fi

say "===== 完了 ====="
