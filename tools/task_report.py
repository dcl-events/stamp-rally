#!/usr/bin/env python3
"""課題申告シート_Claude の申告状況から「誰が加点されたか」のSlack本文を作る。

  課題は「オールクリアで満額(bundle_pt)」の方式なので、加点＝全項目クリアの瞬間。
  それだけだと達成が無い日は毎回空になるので、申告が1つでも進んだ人も添える。

  前回の状態は tools/state/task_bundle.json に持ち、差分を取る。
  （--no-save を付けると状態を更新しない＝お試し実行用）

  実行: python3 tools/task_report.py
    ランキング日次routineの中で、stamp-rally の gen.py 更新が終わった後に呼ぶ。
    標準出力がそのままSlackスレッドへ貼る本文（該当なしでも1行は出る）。
"""
import argparse
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
STATE = os.path.join(ROOT, "tools", "state", "task_bundle.json")
TIER_LABEL = {"beginner": "ビギナー", "rise": "RISE"}


def load_now():
    """id -> {tier: {name, done, total, cleared, pt}}（課題の対象者だけ）"""
    now = {}
    for p in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        if os.path.basename(p).startswith("_"):
            continue
        d = json.load(open(p, encoding="utf-8"))
        for tier in ("beginner", "rise"):
            blk = d.get(tier)
            if not blk or not blk.get("task_member"):
                continue
            t = blk["tasks"]
            now.setdefault(d["id"], {})[tier] = {
                "name": d.get("name", d["id"]),
                "done": t["done_count"],
                "total": t["total"],
                "cleared": bool(t["bundle_earned"]),
                "pt": t["bundle_earned"] or t["bundle_pt"],
            }
    return now


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true", help="状態ファイルを更新しない")
    args = ap.parse_args()

    now = load_now()
    prev = {}
    if os.path.exists(STATE):
        prev = json.load(open(STATE, encoding="utf-8")).get("livers", {})

    newly, progressed, cleared_total = [], [], {"beginner": 0, "rise": 0}
    for lid, tiers in now.items():
        for tier, cur in tiers.items():
            old = (prev.get(lid) or {}).get(tier) or {}
            if cur["cleared"]:
                cleared_total[tier] += 1
                if not old.get("cleared"):
                    newly.append((cur["name"], tier, cur["pt"]))
            elif cur["done"] > int(old.get("done", 0) or 0):
                progressed.append((cur["name"], tier, cur["done"], cur["total"]))

    out = ["📋 課題申告シート_Claude（自己申告・オールクリアで満額加点）"]
    if newly:
        out.append("🎉 本日オールクリア＝加点")
        for name, tier, pt in sorted(newly, key=lambda x: (x[1], x[0])):
            out.append(f"・{name}（{TIER_LABEL[tier]} +{pt:,}pt）")
    else:
        out.append("🎉 本日オールクリア＝加点：なし")
    if progressed:
        out.append("")
        out.append("📝 申告が進んだ人")
        for name, tier, done, total in sorted(progressed, key=lambda x: (x[1], x[0])):
            out.append(f"・{name}（{TIER_LABEL[tier]} {done}/{total}）")
    out.append("")
    out.append(
        f"✅ 現在オールクリア：ビギナー {cleared_total['beginner']}名 ／ RISE {cleared_total['rise']}名"
    )
    print("\n".join(out))

    if not args.no_save:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({"livers": now}, open(STATE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
