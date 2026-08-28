#!/usr/bin/env python3
"""スタンプラリーのライバー別データ(docs/data/<クリエイターID>.json)を生成する。

  成果 = creator_data(バックステージ日次書き出し)から自動判定
  課題 = 課題申告シート_Claude(自己申告)から判定 ＋ ステータス/URL
  ポイント = 成果は個別加点／課題はオールクリアで満額(bundle)

  実行: ~/Claude/pococha/.venv/bin/python tools/gen.py
"""
import csv
import glob
import json
import os
import re
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 認証: ローカルは service_account.json、Actions は 環境変数 GOOGLE_SERVICE_ACCOUNT(JSON文字列)
CREDS = os.environ.get("SERVICE_ACCOUNT_FILE", "/Users/sukeaki.ito/Claude/pococha/service_account.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1A-WSX4mteR-E5kY8V82dTUqoTcD0GDLA0Uq0qiDTzh4"
TASK_GID = 1676003252      # 課題申告シート_Claude（課題・ステータス・URL・名簿）
CREATOR_GID = 1208970074   # クリエイターデータ_Claude（成果の元データ・毎日更新）

# metric key -> creator_data の列名
METRIC_COL = {
    "diamond":       "ダイヤモンド",
    "battle":        "LIVE Match数",
    "live_days":     "有効LIVE日数",
    "live_hours":    "LIVE時間",
    "new_followers": "新規フォロワー",
}


def num(v):
    v = (v or "").replace(",", "").strip()
    m = re.match(r"-?\d+(\.\d+)?", v)
    return float(m.group()) if m else 0.0


def parse_hours(v):
    """'107時間 24分 58秒' -> 107.42 (float時間)"""
    v = v or ""
    h = re.search(r"(\d+)\s*時間", v)
    mi = re.search(r"(\d+)\s*分", v)
    s = re.search(r"(\d+)\s*秒", v)
    return (int(h.group(1)) if h else 0) + (int(mi.group(1)) if mi else 0) / 60 + (int(s.group(1)) if s else 0) / 3600


def gclient():
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT"):
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(CREDS, scopes=SCOPES)
    return gspread.authorize(creds)


def read_tab(sh, gid):
    """_Claude系タブ(1行目=最終更新, 2行目=見出し, 3行目〜=データ)を dict のリストで返す。"""
    ws = next(w for w in sh.worksheets() if w.id == gid)
    vals = ws.get_all_values()
    header = vals[1]
    rows = []
    for r in vals[2:]:
        if not any(c.strip() for c in r):
            continue
        rows.append({header[i]: (r[i] if i < len(r) else "") for i in range(len(header))})
    return header, rows


def load_creator_data(sh):
    _, rows = read_tab(sh, CREATOR_GID)
    by_id = {r["クリエイターID"]: r for r in rows if r.get("クリエイターID")}
    return by_id, f"クリエイターデータ_Claude(gid={CREATOR_GID})"


def metric_value(row, metric):
    col = METRIC_COL[metric]
    raw = row.get(col, "")
    return parse_hours(raw) if metric == "live_hours" else num(raw)


def load_task_sheet(sh):
    ws = next(w for w in sh.worksheets() if w.id == TASK_GID)
    vals = ws.get_all_values()
    header = vals[1]  # 1行目=最終更新, 2行目=見出し, 3行目〜=データ
    idx = {h: i for i, h in enumerate(header)}
    rows = [r for r in vals[2:] if any(c.strip() for c in r)]
    return header, idx, rows


def cell(r, idx, name):
    i = idx.get(name)
    return (r[i].strip() if (i is not None and i < len(r)) else "")


def task_done(v):
    """達成日が入っていれば達成。空欄/「—」(非該当)/未入力は未達。"""
    v = (v or "").strip()
    return bool(v) and v != "—"


def build_tier(cfg_tier, prefix, row_cd, r, idx, locked, result_member, task_member):
    """成果=個別加点／課題=オールクリアで満額。1ティア分のブロックを返す。
    result_member / task_member を別々に持つ：
      ・RISE対象者のビギナー成果は反映(result_member=True)するが、ビギナー課題は対象外(task_member=False)。
      ・member=False の項目は見せるがポイント非加算(参考表示)。"""
    results = []
    r_earned = 0
    r_max = 0
    for it in cfg_tier["results"]:
        r_max += it["pt"]
        val = metric_value(row_cd, it["metric"]) if row_cd else 0
        done = val >= it["target"]
        if done:
            r_earned += it["pt"]
        results.append({
            "key": it["key"], "label": it["label"], "icon": it["icon"],
            "pt": it["pt"], "target": it["target"], "unit": it.get("unit", ""),
            "value": round(val, 1) if it["metric"] == "live_hours" else int(val),
            "done": done,
        })

    tcfg = cfg_tier["tasks"]
    items = []
    done_count = 0
    for it in tcfg["items"]:
        col = f"【{prefix}】{it['label']}"
        raw = cell(r, idx, col)
        done = task_done(raw)
        if done:
            done_count += 1
        items.append({"key": it["key"], "label": it["label"], "icon": it["icon"],
                      "done": done, "done_on": raw if done else ""})
    all_done = done_count == len(items) and len(items) > 0
    bundle_earned = tcfg["bundle_pt"] if all_done else 0
    earned = (r_earned if result_member else 0) + (bundle_earned if task_member else 0)
    max_pt = (r_max if result_member else 0) + (tcfg["bundle_pt"] if task_member else 0)

    return {
        "result_member": result_member,
        "task_member": task_member,
        "locked": locked,
        "results": results,
        "tasks": {"items": items, "done_count": done_count, "total": len(items),
                  "all_done": all_done, "bundle_pt": tcfg["bundle_pt"], "bundle_earned": bundle_earned},
        "earned_pt": earned, "max_pt": max_pt,
        "pct": round(earned / max_pt * 100) if max_pt else 0,
    }


def main():
    cfg = json.load(open(os.path.join(ROOT, "config", "thresholds.json"), encoding="utf-8"))
    sh = gclient().open_by_key(SPREADSHEET_ID)
    cd, cd_file = load_creator_data(sh)
    header, idx, rows = load_task_sheet(sh)
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    updated = now.strftime("%Y-%m-%d")
    updated_at = now.strftime("%Y/%m/%d %H:%M") + " JST"

    out_dir = os.path.join(ROOT, "docs", "data")
    os.makedirs(out_dir, exist_ok=True)

    manifest = []
    written = 0
    for r in rows:
        cid = cell(r, idx, "クリエイターID")
        if not cid:
            continue
        rally = cell(r, idx, "ラリー")
        status = cell(r, idx, "ステータス") or "在籍"
        locked = status != "在籍"
        row_cd = cd.get(cid)

        data = {
            "id": cid,
            "name": cell(r, idx, "ライバー名"),
            "username": cell(r, idx, "クリエイターのユーザー名"),
            "manager": cell(r, idx, "クリエイターマネージャー"),
            "backstage": cell(r, idx, "バックステージ"),
            "status": status,
            "period": cfg.get("period_label", ""),
            "updated": updated,
            "updated_at": updated_at,
            "tiers": [],
        }
        mem_b = "ビギナー" in rally
        mem_r = "RISE" in rally
        # ビギナーはクリアしてRISEへ上がる関係。RISEにいる時点でビギナーはクリア済み＝現在の対象はRISE。
        # ビギナー成果はRISE対象者にも反映(mem_b or mem_r)、ビギナー課題はビギナー"のみ"対象＝RISEに上がったら非対象(mem_b and not mem_r)。
        # RISE成果・課題はRISE対象者のみ(mem_r)。
        data["beginner"] = build_tier(cfg["beginner"], "ビギナー", row_cd, r, idx, locked, mem_b or mem_r, mem_b and not mem_r)
        data["rise"] = build_tier(cfg["rise"], "RISE", row_cd, r, idx, locked, mem_r, mem_r)
        data["tiers"] = [t for t, m in (("beginner", mem_b), ("rise", mem_r)) if m]

        json.dump(data, open(os.path.join(out_dir, f"{cid}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
        written += 1
        manifest.append({"id": cid, "name": data["name"], "tiers": data["tiers"], "status": status})

    json.dump({"updated": updated, "source": cd_file, "count": written, "livers": manifest},
              open(os.path.join(out_dir, "_manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"creator_data: {cd_file}")
    print(f"申告シート: {len(rows)}行")
    print(f"生成: docs/data/*.json = {written}件")


if __name__ == "__main__":
    main()
