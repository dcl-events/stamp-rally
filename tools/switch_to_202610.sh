#!/bin/bash
# 10/3にスタンプラリーをRISE1/RISE2へ切替える（手動スイッチ）。
#   ・config/thresholds.json  ← thresholds_202610.json（RISE1/RISE2・新項目/点数）
#   ・docs/index.html         ← index_202610.html（RISE2ダークデザイン・タブ名RISE1/RISE2）
#   その後 run-daily.sh を回して 申告シート再構成＋JSON再生成＋公開push まで実行する。
#   ★9月中は実行しない。10月対象月（データ反映が10月）に入ってから実行すること。
set -euo pipefail
REPO="$HOME/Claude/stamp-rally"
cd "$REPO"

# 現行(9月)をバックアップ（gitにも残るが保険）
cp config/thresholds.json config/thresholds_202609_backup.json
echo "・9月configを thresholds_202609_backup.json に退避"

# 差し替え
cp config/thresholds_202610.json config/thresholds.json
cp docs/index_202610.html docs/index.html
echo "・thresholds.json ← RISE1/RISE2 ／ index.html ← RISE2ダーク に差し替え"

# 申告シート再構成（課題列: プロフィール/自己紹介/ミニ登竜門を削除・バトル7日/14日を追加）＋JSON再生成＋公開
echo "・run-daily.sh を実行（申告シート再構成＋ページ再生成＋push）…"
bash tools/run-daily.sh

echo "✅ 切替完了。確認: https://dcl-events.github.io/stamp-rally/?id=<クリエイターID>"
echo "   ランキング合算は当日の tiktok-beginner-rise-ranking-daily で自動反映されます。"
