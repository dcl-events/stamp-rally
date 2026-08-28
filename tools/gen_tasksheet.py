#!/usr/bin/env python3
"""課題申告シート_Claude を「マージ更新」する。ランキングのsnapshotを見て名簿を最新化する。

  ★ 申告セル(課題の達成日)は クリエイターIDキーで必ず温存する（全消し洗い替えしない）。
  ・新規ライバー          → 行を追加（課題セルは空）
  ・継続ライバー          → 識別/ラリー/URLを更新、課題申告は温存
  ・ビギナー→RISE昇格     → ラリー切替（在籍のまま）
  ・RISEから外れた        → 削除せず ステータス=「RISE卒業」（課題は締め＝以降受け付けないがセルは残す）
  ・ビギナーから外れた     → 削除せず ステータス=「ビギナー対象外」
  ※誰も削除しない。

  実行: ~/Claude/pococha/.venv/bin/python tools/gen_tasksheet.py [--dry]

  ★snapshot(event-rankings/data)が必要＝ランキング日次ルーティンの後に走らせる想定（ローカル）。
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS = os.environ.get("SERVICE_ACCOUNT_FILE", "/Users/sukeaki.ito/Claude/pococha/service_account.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1A-WSX4mteR-E5kY8V82dTUqoTcD0GDLA0Uq0qiDTzh4"
TASK_GID = 1676003252
CREATOR_GID = 1208970074
RANK_DATA = "/Users/sukeaki.ito/Claude/event-rankings/data"
BASE_URL = "https://dcl-events.github.io/stamp-rally/?id="
DRY = "--dry" in sys.argv

cfg = json.load(open(os.path.join(ROOT, "config", "thresholds.json"), encoding="utf-8"))
BEG_TASKS = [f"【ビギナー】{t['label']}" for t in cfg["beginner"]["tasks"]["items"]]
RISE_TASKS = [f"【RISE】{t['label']}" for t in cfg["rise"]["tasks"]["items"]]
IDENT = ["ライバー名", "クリエイターID", "クリエイターのユーザー名", "クリエイターマネージャー", "バックステージ"]
HEADER = IDENT + ["ラリー", "ステータス", "個別URL"] + BEG_TASKS + RISE_TASKS


def col_a1(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def read_tab(sh, gid):
    ws = next(w for w in sh.worksheets() if w.id == gid)
    vals = ws.get_all_values()
    header = vals[1] if len(vals) > 1 else []
    rows = [{header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
            for r in vals[2:] if any(c.strip() for c in r)]
    return ws, header, rows


def main():
    beg = json.load(open(f"{RANK_DATA}/beginner_snapshot.json"))
    rise = json.load(open(f"{RANK_DATA}/rise_snapshot.json"))

    if os.environ.get("GOOGLE_SERVICE_ACCOUNT"):
        creds = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"]), scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(CREDS, scopes=SCOPES)
    sh = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)

    _, _, cd_rows = read_tab(sh, CREATOR_GID)
    cd = {r["クリエイターID"]: r for r in cd_rows if r.get("クリエイターID")}
    ws, _, existing_rows = read_tab(sh, TASK_GID)
    existing = {r.get("クリエイターID"): r for r in existing_rows if r.get("クリエイターID")}

    all_ids = set(existing) | set(beg) | set(rise)
    added = graduated = dropped = kept = 0
    out = []
    for cid in all_ids:
        prev = existing.get(cid, {})
        in_b, in_r = cid in beg, cid in rise
        c = cd.get(cid, {})

        # 識別（creator_data優先、無ければ既存行）
        name = c.get("ライバー名") or prev.get("ライバー名", "")
        uname = c.get("クリエイターのユーザー名") or prev.get("クリエイターのユーザー名", "")
        mgr = c.get("クリエイターマネージャー") or prev.get("クリエイターマネージャー", "")
        bs = c.get("バックステージ") or prev.get("バックステージ", "")

        prev_rally = prev.get("ラリー", "")
        if in_b or in_r:  # 現在アクティブ
            rally = "ビギナー・RISE" if (in_b and in_r) else ("ビギナー" if in_b else "RISE")
            status = "在籍"
            if cid not in existing:
                added += 1
            else:
                kept += 1
        else:  # snapshotから消えた＝離脱
            rally = prev_rally
            if "RISE" in prev_rally:
                status = "RISE卒業"; graduated += 1
            elif "ビギナー" in prev_rally:
                status = "ビギナー対象外"; dropped += 1
            else:
                status = prev.get("ステータス", "対象外")

        row = {k: v for k, v in zip(IDENT, [name, cid, uname, mgr, bs])}
        row["ラリー"] = rally
        row["ステータス"] = status
        row["個別URL"] = BASE_URL + cid
        # 課題セル：既存を温存。新規/非該当は空 or 「—」
        active_b = in_b or ("ビギナー" in prev_rally)
        active_r = in_r or ("RISE" in prev_rally)
        for col in BEG_TASKS:
            row[col] = prev.get(col, "") if cid in existing else ("" if active_b else "—")
        for col in RISE_TASKS:
            row[col] = prev.get(col, "") if cid in existing else ("" if active_r else "—")
        out.append(row)

    order = {"在籍": 0, "RISE卒業": 1, "ビギナー対象外": 2}
    torder = {"ビギナー": 0, "ビギナー・RISE": 1, "RISE": 2}
    out.sort(key=lambda r: (order.get(r["ステータス"], 9), torder.get(r["ラリー"], 9), r["クリエイターマネージャー"], r["ライバー名"]))

    print(f"対象ID {len(all_ids)}／新規 {added}・継続 {kept}・RISE卒業 {graduated}・ビギナー対象外 {dropped}")
    if DRY:
        print("[dry-run] 書き込みしない。先頭3行:")
        for r in out[:3]:
            print("  ", r["ライバー名"], r["ラリー"], r["ステータス"])
        return

    JST = timezone(timedelta(hours=9))
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    width = len(HEADER)
    grid = [[f"最終更新: {stamp} (JST)"] + [""] * (width - 1), HEADER]
    grid += [[r.get(h, "") for h in HEADER] for r in out]
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)
    if ws.row_count < len(grid):
        ws.add_rows(len(grid) - ws.row_count)
    ws.clear()
    ws.update(values=grid, range_name=f"A1:{col_a1(width)}{len(grid)}", value_input_option="RAW")
    ws.freeze(rows=2)
    print(f"書き込み完了: {len(out)}行 (A1:{col_a1(width)}{len(grid)})")


if __name__ == "__main__":
    main()
