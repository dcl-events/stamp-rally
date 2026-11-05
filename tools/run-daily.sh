#!/bin/bash
# DCL RISEチェックシート 日次更新（ローカル）。ランキング日次routineの後に走らせる。
#   gen_tasksheet.py … RISEチェックシート_Claude を更新
#                      （当月ダイヤ/バトル回数/配信状況などを最新化・ランキング列=前月ptでRISE卒業/1/2/3へ振り分け）
#   ※スタンプラリー廃止（2026-11）に伴い、旧 gen.py（スタンプページ再生成）と git push は停止。
#     シートは gspread で直接書くため push 不要。スタンプのサイト(docs/)は最終状態で凍結。
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

# RISEチェックシートの名簿更新（変化が無ければ内部でスキップ）
if ! "$PY" tools/gen_tasksheet.py >>"$LOG" 2>&1; then
  say "❌ gen_tasksheet 失敗"; exit 1
fi

say "===== 完了 ====="
