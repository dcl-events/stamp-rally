#!/usr/bin/env python3
"""RISEチェックシート_Claude を更新する（スタンプラリー廃止に伴う転換・2026-11）。

  スタンプラリーを一旦廃止したため、旧「課題申告シート」から
    参加状況 / 個別URL / 課題列（【RISE2】×4・【RISE1】×3）
  を廃し、ライバーを当月の RISE リーグに振り分ける「RISEチェックシート」にする。

  ■ ランキング列（旧ラリー）＝当月の所属リーグ。前月ポイントで決まる。
     ソース: event-rankings/data/prev_month_points_<前月>.json（build_prev_month_points.py 生成）
     判定（daily_rise.py / events.json の entry_min_prev_pt・milestone_pt と厳密一致）:
        前月pt ≥ 400万            → RISE卒業
        200万 ≤ 前月pt < 400万    → RISE1
        20万  ≤ 前月pt < 200万    → RISE2
        前月pt < 20万             → RISE3（最下位リーグ・新設）

  ■ 名簿は既存行を温存（誰も削除しない）。スタンプ廃止で snapshot からの自動取り込みは停止。
  ■ 人が入力する「バトルDM可」(可/—) は温存（ランキングサイトの ⚔ バッジ入力元・battle_dm.py が列名で読む）。
  ■ 当月ダイヤ/バトル回数/ダイヤ獲得割合/初配信日/Liny URL/配信状況 は creator_data＋マスターから毎回更新。

  実行: ~/Claude/pococha/.venv/bin/python tools/gen_tasksheet.py [--dry]
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import firstbcast   # 初配信日を日次TSVから再構成（凍結キャッシュ）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS = os.environ.get("SERVICE_ACCOUNT_FILE", "/Users/sukeaki.ito/Claude/pococha/service_account.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1A-WSX4mteR-E5kY8V82dTUqoTcD0GDLA0Uq0qiDTzh4"
TASK_GID = 1676003252       # RISEチェックシート_Claude
CREATOR_GID = 1208970074    # クリエイターデータ_Claude
MASTER_GID = 1135233534     # 【管理】ライバー一覧マスター（Liny URL / 初配信日の取得元）
PARTNER_GID = 1089474990    # 【管理】パートナー経由ライバー一覧（マスターに無い人のLiny URLを補う）
RANK_DATA = "/Users/sukeaki.ito/Claude/event-rankings/data"
GRACE_DAYS = 7   # 対象月の経過がこの日数以内なら「配信ゼロ」は判定待ち扱い（月初の誤検知防止）
DRY = "--dry" in sys.argv

# RISE リーグのしきい値（前月ポイント）。events.json の entry_min_prev_pt / milestone_pt と一致。
RISE_GRAD = 4_000_000   # これ以上は RISE1 卒業
RISE1_MIN = 2_000_000   # RISE1 エントリー（前月200万pt）
RISE2_MIN = 200_000     # RISE2 エントリー（前月20万pt）／これ未満は RISE3

IDENT = ["ライバー名", "クリエイターID", "クリエイターのユーザー名", "クリエイターマネージャー", "バックステージ"]
# 初配信日は クリエイターのユーザー名(C) と クリエイターマネージャー の間＝D列
HEAD_A = IDENT[:3] + ["初配信日"] + IDENT[3:] + ["ランキング", "当月ダイヤ", "バトル回数", "ダイヤ獲得割合", "Liny URL"]  # A〜K
HUMAN_COLS = ["バトルDM可"]   # L列（人が入力。可/— のプルダウン。ランキングサイトの ⚔ バッジ入力元）
HEAD_B = ["配信状況", "参加日", "参加時順位"]   # 末尾（このスクリプトが持つ）＝M〜O列
# 参加日/参加時順位＝当月そのリーグのランキングに初めて載った日とその日の順位。
# event-rankings の日次（daily_rise/daily_beginner/daily_rise3）が data/debut_records.json に記録する。
HEADER = HEAD_A + HUMAN_COLS + HEAD_B
KEEP_COLS = HUMAN_COLS   # 部分更新で触らない（人が入力する）列


def classify(pt, cur_dia):
    """当月の所属リーグ。RISE1/RISE2/卒業は前月ptで確定。
    RISE3 は参加条件＝当月ダイヤ≥1。前月pt<20万でも当月ダイヤが無ければ「RISE3参加前」。"""
    if pt >= RISE_GRAD:
        return "RISE卒業"
    if pt >= RISE1_MIN:
        return "RISE1"
    if pt >= RISE2_MIN:
        return "RISE2"
    return "RISE3" if cur_dia >= 1 else "RISE3参加前"


def prev_ym_key(ym):
    """'2026-11' -> '202610'（前月のキー）"""
    y, m = int(ym[:4]), int(ym[5:7])
    m -= 1
    if m == 0:
        y -= 1
        m = 12
    return f"{y}{m:02d}"


def col_a1(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def apply_dm_validation(ws):
    """「バトルDM可」列だけに プルダウン(可/—) を貼り、旧スタンプ課題列の検証/ブラックアウト書式を一掃する。
    全書き換え（見出し/レイアウト変更）時にだけ呼ぶ。"""
    hfirst = HEADER.index(HUMAN_COLS[0])
    nrows = max(ws.row_count, 1000)
    ncf = 0
    for s in ws.spreadsheet.fetch_sheet_metadata(
            {"fields": "sheets(properties(sheetId),conditionalFormats)"})["sheets"]:
        if s["properties"]["sheetId"] == TASK_GID:
            ncf = len(s.get("conditionalFormats", []))
    reqs = [
        # 旧課題列などに残った検証を全列クリア → バトルDM可だけに再設定
        {"setDataValidation": {"range": {"sheetId": TASK_GID, "startRowIndex": 2, "endRowIndex": nrows,
                                         "startColumnIndex": 0, "endColumnIndex": len(HEADER)}}},
        {"setDataValidation": {"range": {"sheetId": TASK_GID, "startRowIndex": 2, "endRowIndex": nrows,
                                         "startColumnIndex": hfirst, "endColumnIndex": hfirst + 1},
                               "rule": {"condition": {"type": "ONE_OF_LIST",
                                                      "values": [{"userEnteredValue": "可"}, {"userEnteredValue": "—"}]},
                                        "showCustomUi": True, "strict": False}}},
    ]
    # 旧スタンプ課題列のブラックアウト条件付き書式を全削除
    for i in range(ncf - 1, -1, -1):
        reqs.append({"deleteConditionalFormatRule": {"sheetId": TASK_GID, "index": i}})
    ws.spreadsheet.batch_update({"requests": reqs})
    print(f"バトルDM可の検証を再整列＋旧課題列の検証/書式を一掃（条件付き書式 {ncf}件削除）")


def num(v):
    v = (v or "").replace(",", "").strip()
    m = re.match(r"-?\d+(\.\d+)?", v)
    return float(m.group()) if m else 0.0


def parse_hours(v):
    """'107時間 24分 58秒' -> 107.42 / '44分 15秒' -> 0.74 / '-' -> 0"""
    v = v or ""
    h = re.search(r"(\d+)\s*時間", v)
    mi = re.search(r"(\d+)\s*分", v)
    s = re.search(r"(\d+)\s*秒", v)
    return (int(h.group(1)) if h else 0) + (int(mi.group(1)) if mi else 0) / 60 + (int(s.group(1)) if s else 0) / 3600


def read_tab(sh, gid):
    ws = next(w for w in sh.worksheets() if w.id == gid)
    vals = ws.get_all_values()
    header = vals[1] if len(vals) > 1 else []
    rows = [{header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
            for r in vals[2:] if any(c.strip() for c in r)]
    return ws, header, rows


def target_month(cd_rows):
    """creator_data の「データ期間」から対象月(YYYY-MM)を取る。無ければ 今日(UTC)-2日 の月。"""
    for r in cd_rows:
        m = re.match(r"(\d{4})-(\d{2})", (r.get("データ期間") or "").strip())
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    d = datetime.now(timezone.utc) - timedelta(days=2)
    return d.strftime("%Y-%m")


def mlabel(ym):
    """'2026-09' -> '9月'"""
    return f"{int(ym[5:7])}月" if re.match(r"\d{4}-\d{2}$", ym or "") else (ym or "?")


def live_state(c, ym, elapsed):
    """配信状況の文言を返す。c=creator_dataの行(なければNone)"""
    if not c:
        return "⚫️ 名簿なし（要確認）", "dead"
    h = parse_hours(c.get("LIVE時間"))
    d = num(c.get("有効LIVE日数"))
    ph = parse_hours(c.get("先月のLIVE時間（時間）"))
    pd = num(c.get("先月の有効LIVE日数"))
    if h > 0:
        return f"🟢 配信中（{int(d)}日 / {h:.1f}h）", "live"
    if elapsed <= GRACE_DAYS:
        return f"⏸ 判定待ち（{mlabel(ym)}は{elapsed}日経過）", "wait"
    if ph > 0:
        return f"🔴 {mlabel(ym)}は未配信（先月 {int(pd)}日 / {ph:.1f}h）", "stop"
    return f"🔴 未配信（先月も0）", "stop"


def load_master_map(sh):
    """【管理】ライバー一覧マスターから {配信アプリID(=TikTokクリエイターID) → {liny, first}} を作る。
    マスターは3行ヘッダ（3行目=実見出し、4行目〜データ）。
      ・liny  = D列「Liny個別トークページ」（空ならE列「Liny友達ID」からテンプレ生成）
      ・first = U列「初配信日 *自動反映」をそのまま採用（#N/A等の数式エラーは空に落とす）"""
    try:
        mw = next(w for w in sh.worksheets() if w.id == MASTER_GID)
        mv = mw.get_all_values()
    except Exception as e:
        print(f"! マスター読込に失敗: {e} → Liny URL/初配信日は空で継続")
        return {}
    if len(mv) < 4:
        return {}
    hdr = mv[2]

    def find(sub):
        for i, h in enumerate(hdr):
            if sub in h:
                return i
        return -1

    i_sid = find("配信アプリID")
    i_url = find("Liny個別トーク")
    i_fid = find("友達ID")
    i_first = find("初配信日")
    if i_sid < 0:
        print("! マスターに『配信アプリID』列が見つからない → Liny URL/初配信日は空で継続")
        return {}
    m = {}
    for r in mv[3:]:
        sid = r[i_sid].strip() if len(r) > i_sid else ""
        if not sid:
            continue
        url = r[i_url].strip() if (i_url >= 0 and len(r) > i_url) else ""
        if not url and i_fid >= 0 and len(r) > i_fid and r[i_fid].strip():
            url = f"https://manager.liny.jp/line/visual?show=detail&member={r[i_fid].strip()}"
        first = r[i_first].strip() if (i_first >= 0 and len(r) > i_first) else ""
        if first.startswith("#"):   # マスターU列は数式(自動反映)で#N/A等を返すことがある→持ち込まず空欄に
            first = ""
        if url or first:
            m[sid] = {"liny": url, "first": first}
    # パートナー経由ライバー一覧：マスターに無い人 / マスターでLiny URLが空の人だけ補う
    try:
        pv = next(w for w in sh.worksheets() if w.id == PARTNER_GID).get_all_values()
        ph = pv[2]
        pf = lambda sub: next((i for i, h in enumerate(ph) if sub in h), -1)
        p_sid, p_url, p_fid = pf("配信アプリID"), pf("Liny個別トーク"), pf("友達ID")
        added = 0
        for r in pv[3:] if p_sid >= 0 else []:
            sid = r[p_sid].strip() if len(r) > p_sid else ""
            if not sid or m.get(sid, {}).get("liny"):
                continue
            url = r[p_url].strip() if (p_url >= 0 and len(r) > p_url) else ""
            if not url and p_fid >= 0 and len(r) > p_fid and r[p_fid].strip():
                url = f"https://manager.liny.jp/line/visual?show=detail&member={r[p_fid].strip()}"
            if url:
                m.setdefault(sid, {"liny": "", "first": ""})["liny"] = url
                added += 1
        print(f"パートナー経由一覧から Liny URL 補完: {added}件")
    except Exception as e:
        print(f"! パートナー経由一覧の読込に失敗: {e} → マスター分のみで継続")
    nl = sum(1 for v in m.values() if v.get("liny")); nf = sum(1 for v in m.values() if v.get("first"))
    print(f"マスター突合: Liny URL {nl}件 / 初配信日(U列) {nf}件（S列キー）")
    return m


def main():
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT"):
        creds = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"]), scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(CREDS, scopes=SCOPES)
    sh = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)

    _, _, cd_rows = read_tab(sh, CREATOR_GID)
    cd = {r["クリエイターID"]: r for r in cd_rows if r.get("クリエイターID")}
    ws, cur_header, existing_rows = read_tab(sh, TASK_GID)
    existing = {r.get("クリエイターID"): r for r in existing_rows if r.get("クリエイターID")}
    master_map = load_master_map(sh)
    firstbc, fb_new = firstbcast.update()
    print(f"初配信日 再構成キャッシュ: {len(firstbc)}名（今回新規 {fb_new}）")
    # ①手動オーバーライド（最優先・凍結）＝Backstageの「初回LIVE」など人が確定した初配信日を {cid:"YYYY/MM/DD"} で置く。
    #   マスターU列に無い人（改名で再構成が誤るケース等）をここで正す。優先順位は ①手動 → ②マスターU → ③再構成。
    try:
        manual_first = json.load(open(os.path.join(ROOT, "data", "first_broadcast_manual.json"), encoding="utf-8"))
    except (ValueError, OSError):
        manual_first = {}
    if manual_first:
        print(f"初配信日 手動オーバーライド: {len(manual_first)}名")

    ym = target_month(cd_rows)
    asof = datetime.now(timezone.utc) - timedelta(days=2)   # backstage の実績反映は約2日遅れ
    elapsed = asof.day if asof.strftime("%Y-%m") == ym else 31

    # 前月ポイントで当月リーグを決める（RISE卒業/RISE1/RISE2/RISE3）。
    pkey = prev_ym_key(ym)
    ppt_path = f"{RANK_DATA}/prev_month_points_{pkey}.json"
    try:
        prev_pt = json.load(open(ppt_path))
    except Exception as e:
        prev_pt = {}
    if not prev_pt:
        # 前月ポイントが無いと全員が RISE3 になってしまう＝ランキング列を壊す。fail-closed で中止。
        print(f"❌ 前月ポイント {ppt_path} が読めない/空。ランキング分類ができないため書き込み中止。")
        sys.exit(1)
    print(f"前月ポイント: {os.path.basename(ppt_path)}（{len(prev_pt)}名）で当月リーグを判定")

    # RISEランキングの snapshot（今朝の daily_rise3/daily_beginner/daily_rise が更新済み）から
    # 当月ランキングに載った人を取り込む＝新規ユーザーを毎朝そろえる。昇格はランキング列(前月pt)で自動反映。
    #   ★呼び出し元ルーティンは daily_* の後に gen_tasksheet を回すこと（古い snapshot を読まないため）。
    snap_name = {}
    snap_ids = set()
    for fn in ("rise_snapshot.json", "beginner_snapshot.json", "rise3_snapshot.json"):
        try:
            s = json.load(open(f"{RANK_DATA}/{fn}"))
        except Exception as e:
            print(f"! {fn} 読込失敗: {e}")
            continue
        ranks = s.get("ranks", s) if isinstance(s, dict) else {}
        for cid, info in ranks.items():
            snap_ids.add(cid)
            if isinstance(info, dict) and info.get("name"):
                snap_name.setdefault(cid, info["name"])
    # 参加日/参加時順位（当月そのリーグに初めて載った日とその順位）。
    #   ★RANK_DATA 側の日次が先に回っている前提（snapshot と同じ）。無ければ空欄のまま＝壊さない。
    debut_by_league = {}
    try:
        _d = json.load(open(f"{RANK_DATA}/debut_records.json"))
        if _d.get("month") == ym:
            debut_by_league = _d.get("leagues") or {}
            print(f"参加日記録: " + " / ".join(f"{k} {len(v)}名" for k, v in debut_by_league.items()))
        else:
            print(f"! debut_records.json は {_d.get('month')} の記録（対象月 {ym} と不一致）→ 参加日は空欄")
    except Exception as e:
        print(f"! debut_records.json 読込失敗: {e} → 参加日は空欄")

    new_from_snap = snap_ids - set(existing)
    if new_from_snap:
        print(f"RISEランキングからの新規取り込み: {len(new_from_snap)}名")

    # 名簿＝既存行（温存・誰も削除しない）＋ 当月RISEランキングに載った新規。
    all_ids = set(existing) | snap_ids
    out = []
    from collections import Counter
    tier_cnt = Counter()
    for cid in all_ids:
        prev = existing.get(cid, {})
        c = cd.get(cid, {})

        name = c.get("ライバー名") or prev.get("ライバー名", "") or snap_name.get(cid, "")
        uname = c.get("クリエイターのユーザー名") or prev.get("クリエイターのユーザー名", "")
        mgr = c.get("クリエイターマネージャー") or prev.get("クリエイターマネージャー", "")
        bs = c.get("バックステージ") or prev.get("バックステージ", "")

        _dia = num(c.get("ダイヤモンド", ""))
        league = classify(num_or_zero(prev_pt.get(cid)), _dia)
        tier_cnt[league] += 1

        lstate, lcode = live_state(cd.get(cid), ym, elapsed)

        row = {k: v for k, v in zip(IDENT, [name, cid, uname, mgr, bs])}
        row["ランキング"] = league
        row["当月ダイヤ"] = c.get("ダイヤモンド", "")
        row["バトル回数"] = c.get("LIVE Match数", "")
        _mdia = num(c.get("LIVE Matchで獲得したダイヤモンド数", ""))
        row["ダイヤ獲得割合"] = f"{_mdia / _dia * 100:.2f}%" if _dia > 0 else ""
        row["Liny URL"] = master_map.get(cid, {}).get("liny", "")
        # 初配信日＝①手動オーバーライド → ②マスターU列 → ③日次TSV再構成 の優先順。
        #   ★再構成(firstbcast)は「有効LIVE日数0→1 かつ 先月0」で検出するが、月替わりで日数は毎月0に戻るため
        #     「先月休んで今月再開した既存ライバー」を新規デビューと誤認する（例:mimi0610.jp 実06/07→誤11/02）。
        #     ①はBackstageの「初回LIVE」等で人が確定した値、②マスターが直ったので優先、③は①②に無い人の最終保険。
        row["初配信日"] = manual_first.get(cid) or master_map.get(cid, {}).get("first", "") or firstbc.get(cid, "")
        row["配信状況"] = lstate
        # 参加日/参加時順位は「いま所属しているリーグ」の記録を出す（RISE卒業/RISE3参加前は空欄）
        _rec = (debut_by_league.get({"RISE1": "rise1", "RISE2": "rise2", "RISE3": "rise3"}
                                    .get(league, ""), {}) or {}).get(cid)
        row["参加日"] = _rec["date"].replace("-", "/") if _rec else ""
        row["参加時順位"] = f"{_rec['rank']}位/{_rec['total']}人" if _rec else ""
        # 人が入れた値（バトルDM可）はそのまま持ち越す
        for col in HUMAN_COLS:
            row[col] = (prev.get(col, "") if cid in existing else "").strip()
        out.append(row)

    print(f"対象月 {ym}（データ反映 〜{asof:%m-%d}／{elapsed}日経過）／前月={pkey[:4]}-{pkey[4:]}")
    print(f"名簿 {len(out)}名　リーグ分類: {dict(tier_cnt)}")

    if DRY:
        print("[dry-run] 書き込みしない。先頭8行:")
        for r in sorted(out, key=_sortkey)[:8]:
            print("  ", r["ライバー名"], "|", r["ランキング"], "|", r["配信状況"])
        print("[dry-run] 参加日が入る例（RISE3）:")
        for r in [x for x in sorted(out, key=_sortkey) if x["参加日"]][:6]:
            print(f'   {r["ライバー名"]} | {r["ランキング"]} | 参加日 {r["参加日"]} | {r["参加時順位"]}')
        return

    JST = timezone(timedelta(hours=9))
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    note = (f"最終更新: {stamp} (JST)　／　対象月: {ym[:4]}年{int(ym[5:7])}月（データ反映 〜{asof:%m-%d}）"
            f"　／　ランキング列=前月({pkey[:4]}-{pkey[4:]})ポイントで RISE卒業/1/2/3 を判定（RISE3は当月ダイヤ≥1が参加条件・未満は「RISE3参加前」）")
    width = len(HEADER)
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)

    # 名簿(ID集合)・見出しが変わっていなければ、自分が持つ列だけ部分更新（人入力の「バトルDM可」には触らない）。
    same_roster = (cur_header == HEADER) and (set(existing) == {r["クリエイターID"] for r in out})
    if same_roster:
        by_id = {r["クリエイターID"]: r for r in out}
        ordered = [by_id[r["クリエイターID"]] for r in existing_rows]
        a_vals = [[r.get(h, "") for h in HEAD_A] for r in ordered]
        b_vals = [[r.get(h, "") for h in HEAD_B] for r in ordered]
        n = len(ordered) + 2
        q0 = col_a1(len(HEAD_A) + len(KEEP_COLS) + 1)
        data = [
            {"range": "A1", "values": [[note]]},
            {"range": f"A3:{col_a1(len(HEAD_A))}{n}", "values": a_vals},
            {"range": f"{q0}3:{col_a1(width)}{n}", "values": b_vals},
        ]
        if [[r.get(h, "") for h in HEAD_A + HEAD_B] for r in existing_rows] == \
           [[r.get(h, "") for h in HEAD_A + HEAD_B] for r in ordered]:
            print("名簿・内容ともに変更なし → 書き込みスキップ")
            return
        ws.batch_update(data, value_input_option="RAW")
        print(f"部分更新: {len(ordered)}行（A〜{col_a1(len(HEAD_A))} と {q0}〜{col_a1(width)}／バトルDM可は非更新）")
        return

    # 見出し変更 or 名簿変更 → リーグ順に並べて全面書き込み
    out.sort(key=_sortkey)
    grid = [[note] + [""] * (width - 1), HEADER]
    grid += [[r.get(h, "") for h in HEADER] for r in out]
    if ws.row_count < len(grid):
        ws.add_rows(len(grid) - ws.row_count)
    ws.clear()
    ws.update(values=grid, range_name=f"A1:{col_a1(width)}{len(grid)}", value_input_option="RAW")
    ws.freeze(rows=2)
    apply_dm_validation(ws)
    print(f"全面書き込み: {len(out)}行 (A1:{col_a1(width)}{len(grid)})＋リーグ順ソート")


def num_or_zero(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


_TORDER = {"RISE卒業": 0, "RISE1": 1, "RISE2": 2, "RISE3": 3, "RISE3参加前": 4}


def _sortkey(r):
    return (_TORDER.get(r["ランキング"], 9), r.get("クリエイターマネージャー", ""), r.get("ライバー名", ""))


if __name__ == "__main__":
    main()
