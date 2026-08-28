# DCL スタンプラリー (stamp-rally)

TikTok LIVE ライバー向けスタンプラリー（ビギナー / RISE）。
各ライバーの個別ページを **1枚のHTMLテンプレ + ライバー別JSON** で配信する。

- 公開URL: `https://dcl-events.github.io/stamp-rally/?id=<クリエイターID>`
- 成果 = 個別加点（自動・バックステージ由来）／課題 = オールクリアで満額（自己申告）
- 獲得ポイントは今後ビギナー/RISEランキングへ合算予定

## 構成
- `docs/index.html` … データ駆動テンプレ（?id= でJSONを読む）
- `docs/data/<ID>.json` … ライバー別データ（自動生成）
- `config/thresholds.json` … しきい値・ポイントの正本
- `tools/gen.py` … 成果(クリエイターデータ_Claude)＋課題(課題申告シート_Claude)→ JSON生成【Actionsで日次】
- `tools/gen_tasksheet.py` … 課題申告シートの名簿マージ更新（申告セル温存・卒業フラグ）【ローカル・ランキング日次の後段】

## データ源（Googleスプレッドシート「TikTokLIVEハイタッチ確認」）
- 成果: `クリエイターデータ_Claude` (gid=1208970074) 毎日更新
- 課題/名簿/URL: `課題申告シート_Claude` (gid=1676003252) 自己申告

## 自動化
- GitHub Actions が毎日 `gen.py` を実行 → `docs/data/*.json` を更新 → Pages公開（PC不要）
- サービスアカウントJSONはリポジトリ Secret `GOOGLE_SERVICE_ACCOUNT` に格納
