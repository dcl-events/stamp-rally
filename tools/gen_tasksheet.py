#!/usr/bin/env python3
"""課題申告シート_Claude を「マージ更新」する。ランキングのsnapshotを見て名簿を最新化する。

  ★ 申告セル(課題の達成日)は クリエイターIDキーで必ず温存する（全消し洗い替えしない）。
  ・新規ライバー          → 行を追加（課題セルは空）
  ・継続ライバー          → 識別/ラリー/URLを更新、課題申告は温存
  ・ビギナー→RISE昇格     → ラリー切替（在籍のまま）
  ・名簿から外れた        → 削除せず 参加状況=「🔴 対象外」（時期は最終参加月、種別はラリー列）
  ※誰も削除しない。

  ■ 月が分かるようにするための列（2026-09-02 追加）
    G 参加状況     … 当月の状態のみ：🟢 9月 参加中／🟡 9月 参加中（反映待ち）／🔴 対象外
    Q 初回参加月   … 初めて名簿に載った月（以後ずっと温存）
    R 最終参加月   … 最後に名簿に載っていた月（在籍中は対象月に追従／離脱で凍結）
    S 配信状況     … creator_data の当月LIVE時間から自動（月初は「判定待ち」）
    T URL送付      … 送る / 保留（未配信） / 不要（◯月で終了）＝これで送付対象を絞る
    対象月 = creator_data の「データ期間」＝ backstage が今返している月（今日-2日基準）

  ■ 月替わりの扱い（2026-09-04 追加）
    ランキング側は月スコープ（snapshot が {"month","ranks"} 形式・月初に名簿がリセットされる）。
    新しい月の snapshot に居ない＝離脱ではないので、対象月の経過が GRACE_DAYS 以内、
    または snapshot がまだ当月に更新されていない間は、誰も降格させず在籍のまま据え置く。

  実行: ~/Claude/pococha/.venv/bin/python tools/gen_tasksheet.py [--dry]

  ★snapshot(event-rankings/data)が必要＝ランキング日次ルーティンの後に走らせる想定（ローカル）。
"""
import json
import os
import re
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
MASTER_GID = 1135233534   # 【管理】ライバー一覧マスター（Liny URLの取得元）
RANK_DATA = "/Users/sukeaki.ito/Claude/event-rankings/data"
BASE_URL = "https://dcl-events.github.io/stamp-rally/?id="
GRACE_DAYS = 7   # 対象月の経過がこの日数以内なら「配信ゼロ」は判定待ち扱い（月初の誤検知防止）
DRY = "--dry" in sys.argv

cfg = json.load(open(os.path.join(ROOT, "config", "thresholds.json"), encoding="utf-8"))
BEG_TASKS = [f"【ビギナー】{t['label']}" for t in cfg["beginner"]["tasks"]["items"]]
RISE_TASKS = [f"【RISE】{t['label']}" for t in cfg["rise"]["tasks"]["items"]]
IDENT = ["ライバー名", "クリエイターID", "クリエイターのユーザー名", "クリエイターマネージャー", "バックステージ"]
# 初配信日(マスターU列)は クリエイターのユーザー名(C) と クリエイターマネージャー の間に入れる＝D列
HEAD_A = IDENT[:3] + ["初配信日"] + IDENT[3:] + ["ラリー", "参加状況", "Liny URL", "個別URL"]  # A〜J
TASK_COLS = BEG_TASKS + RISE_TASKS                                  # K〜R（人が入力する。触らない）
HEAD_B = ["初回参加月", "最終参加月", "配信状況", "URL送付"]          # S〜V（このスクリプトが持つ）
HEADER = HEAD_A + TASK_COLS + HEAD_B


def col_a1(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def sort_participation(ws, ndata):
    """参加中を上・対象外/卒業を下へ並べ替える（行ごとサーバ側ソート＝課題も一緒に安全に移動）。
    参加状況G降順(🟢参加中→🟡反映待ち→🔴対象外)→配信状況S降順→マネージャー→名前。"""
    if ndata <= 1:
        return
    ws.spreadsheet.batch_update({"requests": [{"sortRange": {
        "range": {"sheetId": TASK_GID, "startRowIndex": 2, "endRowIndex": 2 + ndata,
                  "startColumnIndex": 0, "endColumnIndex": len(HEADER)},
        "sortSpecs": [
            {"dimensionIndex": HEADER.index("参加状況"), "sortOrder": "DESCENDING"},        # 参加中→反映待ち→対象外
            {"dimensionIndex": HEADER.index("ラリー"), "sortOrder": "ASCENDING"},           # RISE→RISE(ビギナー卒業)→ビギナー
            {"dimensionIndex": HEADER.index("配信状況"), "sortOrder": "DESCENDING"},        # 配信状況
            {"dimensionIndex": HEADER.index("クリエイターマネージャー"), "sortOrder": "ASCENDING"},  # マネージャー
            {"dimensionIndex": HEADER.index("ライバー名"), "sortOrder": "ASCENDING"},       # ライバー名
        ]}}]})


def apply_task_validation(ws):
    """課題列だけに プルダウン(達成/—) と ブラックアウト(—を灰で潰す)書式を貼り直す。
    列を追加/移動しても列位置がズレないよう HEADER から課題列の範囲を毎回計算する
    （検証/書式は列位置固定なので、列挿入すると元の位置に残り別の列でエラーになる＝それを自己修復）。
    全書き換え（＝見出し/レイアウト変更）時にだけ呼ぶ。"""
    first = HEADER.index(TASK_COLS[0])
    last = HEADER.index(TASK_COLS[-1])
    nrows = max(ws.row_count, 1000)
    grey = {"red": 0.2, "green": 0.2, "blue": 0.2}
    ncf = 0
    for s in ws.spreadsheet.fetch_sheet_metadata(
            {"fields": "sheets(properties(sheetId),conditionalFormats)"})["sheets"]:
        if s["properties"]["sheetId"] == TASK_GID:
            ncf = len(s.get("conditionalFormats", []))
    reqs = [
        # 全列(A〜末尾)の検証をいったんクリア → 課題列だけに再設定（他列に残った検証を除去）
        {"setDataValidation": {"range": {"sheetId": TASK_GID, "startRowIndex": 2, "endRowIndex": nrows,
                                         "startColumnIndex": 0, "endColumnIndex": len(HEADER)}}},
        {"setDataValidation": {"range": {"sheetId": TASK_GID, "startRowIndex": 2, "endRowIndex": nrows,
                                         "startColumnIndex": first, "endColumnIndex": last + 1},
                               "rule": {"condition": {"type": "ONE_OF_LIST",
                                                      "values": [{"userEnteredValue": "達成"}, {"userEnteredValue": "—"}]},
                                        "showCustomUi": True, "strict": False}}},
    ]
    for i in range(ncf - 1, -1, -1):
        reqs.append({"deleteConditionalFormatRule": {"sheetId": TASK_GID, "index": i}})
    reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": TASK_GID, "startRowIndex": 2, "endRowIndex": nrows,
                    "startColumnIndex": first, "endColumnIndex": last + 1}],
        "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "—"}]},
                        "format": {"backgroundColor": grey, "textFormat": {"foregroundColor": grey}}}}}})
    ws.spreadsheet.batch_update({"requests": reqs})
    print(f"課題列の検証/書式を再整列: {col_a1(first + 1)}〜{col_a1(last + 1)}")


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


def load_snapshot(path, ym):
    """ランキングのsnapshotを読む。
    新形式 {"month": "YYYY-MM", "ranks": {cid: {...}}} / 旧形式 {cid: {...}} の両対応。
    戻り値 (その snapshot の月, ranks)。月が対象月と違う＝まだ当月分に更新されていない。"""
    snap = json.load(open(path))
    if isinstance(snap, dict) and "ranks" in snap:
        return (snap.get("month") or ""), (snap.get("ranks") or {})
    return ym, snap                       # 旧形式は月を持たないので対象月とみなす


def base_of(status):
    """既存行の「参加状況」から 在籍/RISE卒業/ビギナー対象外 を読み戻す。"""
    s = (status or "").strip()
    if not s or "参加中" in s or s == "在籍":
        return "在籍"
    if "RISE卒業" in s:
        return "RISE卒業"
    if "ビギナー対象外" in s:
        return "ビギナー対象外"
    return "対象外"


def load_master_map(sh):
    """【管理】ライバー一覧マスターから {配信アプリID(=TikTokクリエイターID) → {liny, first}} を作る。
    マスターは3行ヘッダ（1行目=空/結合、2行目=大分類、3行目=実見出し、4行目〜データ）。
      ・liny  = D列「Liny個別トークページ」（空ならE列「Liny友達ID」からテンプレ生成）
      ・first = U列「初配信日 *自動反映」（バトル課題『初配信から◯日目』の判定用）"""
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
        if url or first:
            m[sid] = {"liny": url, "first": first}
    nl = sum(1 for v in m.values() if v["liny"]); nf = sum(1 for v in m.values() if v["first"])
    print(f"マスター突合: Liny URL {nl}件 / 初配信日 {nf}件（S列キー）")
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
    master_map = load_master_map(sh)   # {配信アプリID(=クリエイターID) → {liny, first}}

    ym = target_month(cd_rows)
    asof = datetime.now(timezone.utc) - timedelta(days=2)   # backstage の実績反映は約2日遅れ
    elapsed = asof.day if asof.strftime("%Y-%m") == ym else 31

    bmonth, beg = load_snapshot(f"{RANK_DATA}/beginner_snapshot.json", ym)
    rmonth, rise = load_snapshot(f"{RANK_DATA}/rise_snapshot.json", ym)
    # ランキングは月替わりで名簿がリセットされ、当月ptを積んだ人から順に載っていく。
    # そのため「新しい月の snapshot に居ない」は離脱の証拠にならない。
    #   ・snapshot がまだ前月のまま      → 降格判定に使わない（据え置き）
    #   ・対象月の経過が GRACE_DAYS 以内 → 降格判定に使わない（据え置き）
    stale = (bmonth != ym) or (rmonth != ym)
    roster_grace = stale or (elapsed <= GRACE_DAYS)

    all_ids = set(existing) | set(beg) | set(rise)
    added = graduated = dropped = kept = waiting = 0
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
        first_m = (prev.get("初回参加月") or "").strip() or ym       # 初回は一度決めたら動かさない
        last_m = (prev.get("最終参加月") or "").strip()

        if in_b or in_r:  # 現在アクティブ
            rally = "RISE（ビギナー卒業）" if (in_b and in_r) else ("ビギナー" if in_b else "RISE")
            base = "在籍"
            last_m = ym                                            # 在籍中は対象月に追従
            status = f"🟢 {mlabel(ym)} 参加中"
            if cid not in existing:
                added += 1
            else:
                kept += 1
        elif roster_grace and base_of(prev.get("参加状況")) == "在籍" and cid in existing:
            # 月替わり直後 or snapshot が当月未反映。まだランキングに載っていないだけ＝在籍据え置き。
            # 最終参加月は当月の名簿に載るまで進めない（離脱が確定したとき最終参加月を正しく凍結するため）
            rally = prev_rally
            base = "在籍"
            last_m = last_m or ym
            status = f"🟡 {mlabel(ym)} 参加中（反映待ち）"
            waiting += 1
            kept += 1
        else:  # snapshotから消えた＝離脱。参加状況は当月の状態(=対象外)のみ。時期は最終参加月、種別はラリー列が持つ。
            rally = prev_rally
            last_m = last_m or ym
            status = "🔴 対象外"
            if "RISE" in prev_rally:
                base = "RISE卒業"; graduated += 1
            elif "ビギナー" in prev_rally:
                base = "ビギナー対象外"; dropped += 1
            else:
                base = "対象外"

        lstate, lcode = live_state(cd.get(cid), ym, elapsed)
        if base != "在籍":
            send = f"不要（{mlabel(last_m)}で終了）"
        elif lcode == "dead":
            send = "要確認（名簿なし）"
        elif lcode == "stop":
            send = "保留（未配信）"
        else:
            send = "送る"

        row = {k: v for k, v in zip(IDENT, [name, cid, uname, mgr, bs])}
        row["ラリー"] = rally
        row["参加状況"] = status
        row["Liny URL"] = master_map.get(cid, {}).get("liny", "")   # マスターS列(配信アプリID=クリエイターID)で突合
        row["個別URL"] = BASE_URL + cid
        row["初配信日"] = master_map.get(cid, {}).get("first", "")   # マスターU列（バトル課題の起点）
        row["初回参加月"] = first_m
        row["最終参加月"] = last_m
        row["配信状況"] = lstate
        row["URL送付"] = send
        row["_base"] = base
        # 課題セルの対象判定（ページのtask_memberと一致させる）：
        #   ビギナー課題＝純ビギナーのみ(mem_b and not mem_r)／RISE課題＝RISE含む(mem_r)。
        #   対象は「達成/日付だけ温存・それ以外は空欄」で誤「—」も自己修復、対象外は「—」。
        mem_b = "ビギナー" in rally
        mem_r = "RISE" in rally
        beg_app = mem_b and not mem_r
        rise_app = mem_r
        for col in BEG_TASKS:
            old = (prev.get(col, "") if cid in existing else "").strip()
            row[col] = (old if old not in ("", "—") else "") if beg_app else "—"
        for col in RISE_TASKS:
            old = (prev.get(col, "") if cid in existing else "").strip()
            row[col] = (old if old not in ("", "—") else "") if rise_app else "—"
        out.append(row)

    print(f"対象月 {ym}（データ反映 〜{asof:%m-%d}／{elapsed}日経過）")
    print(f"snapshot ビギナー {bmonth}/{len(beg)}名・RISE {rmonth}/{len(rise)}名"
          + ("　※当月未反映のため降格判定は据え置き" if stale else
             f"　※月初{elapsed}日目のため降格判定は据え置き" if roster_grace else ""))
    print(f"対象ID {len(all_ids)}／新規 {added}・継続 {kept}（うち反映待ち {waiting}）"
          f"・RISE卒業 {graduated}・ビギナー対象外 {dropped}")
    from collections import Counter
    print("URL送付:", dict(Counter(r["URL送付"] for r in out)))

    if DRY:
        print("[dry-run] 書き込みしない。先頭5行:")
        for r in out[:5]:
            print("  ", r["ライバー名"], "|", r["参加状況"], "|", r["配信状況"], "|", r["URL送付"])
        return

    JST = timezone(timedelta(hours=9))
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    note = f"最終更新: {stamp} (JST)　／　対象月: {ym[:4]}年{int(ym[5:7])}月（データ反映 〜{asof:%m-%d}）"
    width = len(HEADER)
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)

    # 名簿(ID集合)が変わっていなければ既存の行順を守り、自分が持つ列だけを部分更新する。
    # ＝人が入力する課題列(I〜P)には一切触らない＝入力中の取りこぼしリスクを最小化。
    # ★比較対象は「既存シートのID集合」と「今回算出したID集合」。
    #   （既存行どうしを比べると常に一致してしまい、新規ライバーが書かれない）
    same_roster = (cur_header == HEADER) and (set(existing) == {r["クリエイターID"] for r in out})
    if same_roster:
        by_id = {r["クリエイターID"]: r for r in out}
        ordered = [by_id[r["クリエイターID"]] for r in existing_rows]
        a_vals = [[r.get(h, "") for h in HEAD_A] for r in ordered]
        b_vals = [[r.get(h, "") for h in HEAD_B] for r in ordered]
        n = len(ordered) + 2
        q0 = col_a1(len(HEAD_A) + len(TASK_COLS) + 1)
        data = [
            {"range": "A1", "values": [[note]]},
            {"range": f"A3:{col_a1(len(HEAD_A))}{n}", "values": a_vals},
            {"range": f"{q0}3:{col_a1(width)}{n}", "values": b_vals},
        ]
        if [[r.get(h, "") for h in HEAD_A + HEAD_B] for r in existing_rows] == \
           [[r.get(h, "") for h in HEAD_A + HEAD_B] for r in ordered]:
            sort_participation(ws, len(ordered))  # 無変更でも並びだけは保証（冪等）
            print("名簿・状況ともに変更なし → 書き込みスキップ（並びは維持）")
            return
        ws.batch_update(data, value_input_option="RAW")
        sort_participation(ws, len(ordered))
        print(f"部分更新: {len(ordered)}行（A〜{col_a1(len(HEAD_A))} と {q0}〜{col_a1(width)}／課題列は非更新）＋参加順ソート")
        return

    # 名簿が変わった or 見出しが変わった → 並べ直して全面書き込み
    order = {"在籍": 0, "RISE卒業": 1, "ビギナー対象外": 2}
    torder = {"RISE": 0, "RISE（ビギナー卒業）": 1, "ビギナー": 2}
    out.sort(key=lambda r: (order.get(r["_base"], 9), torder.get(r["ラリー"], 9),
                            r["クリエイターマネージャー"], r["ライバー名"]))
    grid = [[note] + [""] * (width - 1), HEADER]
    grid += [[r.get(h, "") for h in HEADER] for r in out]
    if ws.row_count < len(grid):
        ws.add_rows(len(grid) - ws.row_count)
    ws.clear()
    ws.update(values=grid, range_name=f"A1:{col_a1(width)}{len(grid)}", value_input_option="RAW")
    ws.freeze(rows=2)
    apply_task_validation(ws)   # 列レイアウトが変わった時に課題列の検証/書式を貼り直す（列ズレ自己修復）
    sort_participation(ws, len(out))
    print(f"全面書き込み: {len(out)}行 (A1:{col_a1(width)}{len(grid)})＋参加順ソート")


if __name__ == "__main__":
    main()
