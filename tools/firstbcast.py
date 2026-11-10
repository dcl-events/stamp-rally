#!/usr/bin/env python3
"""初配信日を日次バックステージデータ(creator_data_*.tsv)から再構成し、凍結キャッシュに貯める。

  初配信日 = 「有効LIVE日数」が 0→1 に変わった日（＝初めてLIVE実績が立った日）。
  ★日付は「TSVのファイル名の日付 − 2日」＝データ基準日を使う（Backstageの反映が2日遅れのため。
    ファイル名の日付をそのまま使うと初配信日が2日あとにズレ、ランキング参加日より後になる事故が起きる）。
  誤判定を防ぐため確定条件：
    (a) それより前のスナップショットで その人を「有効LIVE日数=0」として観測している（0→1の遷移を実際に見た）
    (b) その日の「先月の有効LIVE日数」=0（前月までLIVE実績が無い＝本当の新人／月跨ぎの再開を初配信と誤認しない）
  ★一度確定した初配信日は二度と上書きしない（凍結）。
    日次TSVはローテーションで約1か月ぶんしか残らないため、消える前にキャッシュへ貯め続けるのが要点。

  アーカイブ窓(現在≈直近30本)より前にデビュー済みの人は 0→1 を観測できず未確定(空欄)。
  その人の初配信日は マスターU列（生きていれば）か、Backstageの配信履歴を目視で補完する。

  モジュール: from firstbcast import update; cache,new = update(TSV_DIR, CACHE_PATH)  # {cid:"YYYY/MM/DD"}
"""
import csv
import glob
import json
import os
import sys
from datetime import datetime, timedelta

TSV_DIR_DEFAULT = os.path.expanduser("~/Claude/tiktok-automation/out")
LAG_DAYS = 2   # Backstageの実績反映の遅れ。TSVのファイル名の日付 − 2日 ＝ そのデータの基準日（＝実際の配信日）
CACHE_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "first_broadcast.json")


def _num(v):
    try:
        return int(float((v or "0").replace(",", "").strip()))
    except (ValueError, AttributeError):
        return 0


def update(tsv_dir=TSV_DIR_DEFAULT, cache_path=CACHE_DEFAULT):
    """TSVを時系列で走査し、未確定の人だけ初配信日を確定してキャッシュへ。確定済みは凍結。"""
    cache = {}
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except (ValueError, OSError):
            cache = {}
    files = sorted(glob.glob(os.path.join(tsv_dir, "creator_data_*.tsv")))
    seen_zero = set()
    new = 0
    for f in files:
        parts = os.path.basename(f).split("_")
        if len(parts) < 3:
            continue
        try:                               # ファイル名の日付 − 2日 ＝ データ基準日（実際の配信日）
            date = (datetime.strptime(parts[2], "%Y-%m-%d")
                    - timedelta(days=LAG_DAYS)).strftime("%Y/%m/%d")
        except ValueError:
            continue
        try:
            rows = list(csv.reader(open(f, encoding="utf-8"), delimiter="\t"))
        except OSError:
            continue
        if len(rows) < 2:
            continue
        idx = {h.strip(): i for i, h in enumerate(rows[0])}
        iID = idx.get("クリエイターID", -1)
        iDays = idx.get("有効LIVE日数", -1)
        iPrev = idx.get("先月の有効LIVE日数", -1)
        if iID < 0 or iDays < 0:
            continue
        zeros_this = []
        for r in rows[1:]:
            if len(r) <= max(iID, iDays):
                continue
            cid = r[iID].strip()
            if not cid:
                continue
            days = _num(r[iDays]) if len(r) > iDays else 0
            if days > 0:
                prev = _num(r[iPrev]) if (iPrev >= 0 and len(r) > iPrev) else 0
                if cid not in cache and cid in seen_zero and prev == 0:
                    cache[cid] = date
                    new += 1
            else:
                zeros_this.append(cid)
        seen_zero.update(zeros_this)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    return cache, new


def rebuild(tsv_dir=TSV_DIR_DEFAULT, cache_path=CACHE_DEFAULT):
    """いま残っているTSVの窓で再計算し、再計算できた人だけ値を差し替える（窓の外の人は従来値のまま）。
    ファイル名の日付をそのまま使っていた旧キャッシュ（2日あと）を直すための復旧用。"""
    old = {}
    if os.path.exists(cache_path):
        try:
            old = json.load(open(cache_path, encoding="utf-8"))
        except (ValueError, OSError):
            old = {}
    tmp = cache_path + ".rebuild"
    fresh, _ = update(tsv_dir, tmp)
    os.remove(tmp)
    changed = {c: (old.get(c), v) for c, v in fresh.items() if old.get(c) != v}
    merged = dict(old); merged.update(fresh)
    json.dump(merged, open(cache_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0, sort_keys=True)
    return merged, changed


if __name__ == "__main__":
    if "--rebuild" in sys.argv:
        c, ch = rebuild()
        print(f"初配信日キャッシュ 再計算: {len(c)}名（値が変わった人 {len(ch)}名）")
        for cid, (a, b) in list(ch.items())[:50]:
            print(f"  {cid}: {a} → {b}")
    else:
        c, n = update()
        print(f"初配信日キャッシュ: {len(c)}名確定（今回新規 {n}名） -> {CACHE_DEFAULT}")
