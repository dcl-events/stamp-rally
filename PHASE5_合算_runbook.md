# Phase 5：スタンプラリーpt → ランキング合算（GO待ち runbook）

状態：**準備のみ。ライブ未反映。** ユーザーGO（「合算GO」）で下記4ステップを入れる。
決定日：2026-09-10（ユーザー確認済み）

## 確定仕様
- **合算合計で再ランキング**：`表示pt = 基礎pt + スタンプpt`。この合計でソートし直して順位を付け替える。
- **表示**：合計pointを一本で見せ、カードに内訳バッジ `🎯 スタンプ +12,000` を出す。
- **ティア別に足す**（＝卒業者はビギナー分を引き継がない、を自動で満たす）：
  - ビギナーランキング（tiktok-YYYYMM-**newcomer**） ← `beginner.earned_pt`
  - RISEランキング（tiktok-YYYYMM-**rise**） ← `rise.earned_pt`
- スタンプptは `gen.py` が既にメンバー範囲を分けて算出済み：
  - beginner: 成果=純ビギナー＋RISEにも反映(result)、課題=純ビギナーのみ(task)
  - rise: 成果・課題ともRISE対象のみ
  - → RISEランキングに rise.earned_pt を足すだけで「卒業者はRISE側のみ反映」になる。

## 現状の点数式（基礎pt・変更しない）
`base = ダイヤ×10 + Matchダイヤ×5 + Match数×1000 + 継続bonus(keizoku_bonus)`
`point = base + fanbonus`
（CSVの `bonus` 列＝継続ボーナスで使用中。スタンプは別枠で足す＝流用しない）

## データ結合（IDは公開データに出さない）
- 公開は `event-rankings/docs/*.html` のみ。`data/`（snapshot・CSV）は**非公開**＝ここでID突合してよい。
- 突合キー：`event-rankings/data/{beginner,rise}_snapshot.json`（クリエイターID↔name↔pt↔rank、基礎pt降順）と CSV は**同じ並び**＝rank順（行index）で1:1対応。名前+基礎ptを保険キーに。
- スタンプpt源：`stamp-rally/docs/data/_manifest.json`（公開・IDはURLキーで既に公開済み）に earned_pt を持たせる（下記ステップ1）。

## GO時に触る4ステップ

### 1. stamp-rally/tools/gen.py — manifestに earned_pt を持たせる（additive・安全）
`manifest.append(...)` に `"pts"` を追加：
```python
manifest.append({"id": cid, "name": data["name"], "tiers": data["tiers"], "status": status,
                 "pts": {"beginner": data["beginner"]["earned_pt"],
                         "rise":     data["rise"]["earned_pt"]}})
```
（earned_pt は member=False なら 0。締め(locked)でも申告済みptは保持される＝そのまま合算対象）

### 2. event-rankings/build.py — スタンプ合算（config駆動・未設定なら無挙動）
`rows_from_csv` の後段に、ev_cfg に下記フィールドがある時だけ動く合算を足す：
- 新フィールド：`stamp_snapshot`（例 `beginner_snapshot.json`）, `stamp_manifest`（stamp-rally の _manifest.json 絶対パス）, `stamp_tier`（`beginner`/`rise`）, `stamp_label`（既定 `🎯 スタンプ`）
- 処理：snapshotをrank順に並べ、CSV各行へ id を割当（index対応＋name/base_ptで検証）→ `stamp = manifest[id]["pts"][tier]` → `row["stamp"]=stamp` / `row["score"] += stamp`。
- 既存の `rows.sort(key=score, reverse=True)` がそのまま再ランキングになる。
- `render_item` に：`if r.get("stamp",0)>0:` で `🎯 スタンプ +{stamp:,}` バッジを1行出す。

### 3. data/events.json — 対象2イベントにフィールドを足す（＝実質のトグル）
`tiktok-YYYYMM-newcomer` に `stamp_tier:"beginner"` ＋ snapshot/manifest パス、
`tiktok-YYYYMM-rise` に `stamp_tier:"rise"` ＋ 同。**このフィールドを入れる＝ON、外す＝OFF。**
ルール文言（rules）にも「スタンプラリー獲得ポイントを合算」を1項追記。

### 4. 実行順（重要）— build.py の前に stamp-rally gen を回す
`tools/run-daily-tiktok-rankings.sh` の daily_rise の後・build.py の前に：
```
（snapshot確定済み）→ stamp-rally: gen_tasksheet.py → gen.py（_manifest更新）→ build.py
```
※スタンプptはロスター（誰がbeginner/rise）にのみ依存し、pt自体には依存しない＝snapshot確定後にgenを回せば循環しない。

## 検証（GO後）
- `python build.py` を1回 → 既知ライバーで `合計 == 基礎 + スタンプ` を確認。
- 再ランキングの妥当性：スタンプ満額(beginner 50,000 / rise 45,000)で中位が数枚繰り上がる程度（RISE上位は基礎pt桁違いでほぼ不動、ビギナー中〜下位は動く）。
- バッジ表示・順位差分をHTMLで目視。

## ロールバック
events.json の2イベントから stamp_* フィールドを外す → 基礎ptのみの元表示に即戻る（build.py/gen.py のコードは残っても無挙動）。

## 未決/確認ポイント（GO時に一言確認）
- 猶予期間中の卒業者がビギナーランキングに残っている間、そのビギナーランキング側で `beginner.earned_pt`（＝成果分のみ、課題は非対象で0）を足すか。現設計は「各ランキングは自分のティアのearned_ptを足す」で一貫＝足す想定。気になるなら猶予中は0にする分岐を追加可。
