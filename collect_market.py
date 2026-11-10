"""야후 파이낸스에서 코스피·S&P 500 일봉을 매일 수집한다.

- 외부 패키지 없이 파이썬 표준 라이브러리만 사용 (pandas/yfinance 불필요)
- 하루 한 번 실행하면 market_data.csv 에 그날 데이터가 쌓인다 (같은 날 여러 번 실행해도 중복 없음)
- 실행할 때마다 market_data.json 을 다시 만들고, index.html 결과화면 카드용 데이터도 바꿔 쓴다
- 첫 실행일부터 TARGET_DAYS 거래일이 모이면 "수집 완료"를 알려준다
- 지난 3개월 지수 수익률도 계산해 게임 결과화면의 "3개월 전에 투자했다면?" 계산에 쓴다

사용법:  python collect_market.py
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TICKERS = {"^KS11": "코스피", "^GSPC": "S&P 500"}
TARGET_DAYS = 10
HERE = Path(__file__).resolve().parent
CSV_PATH = HERE / "market_data.csv"
JSON_PATH = HERE / "market_data.json"
GAME_PATH = HERE / "index.html"
MARK_START, MARK_END = "<!-- MARKET_DATA:START -->", "<!-- MARKET_DATA:END -->"
FIELDS = ["date", "ticker", "name", "open", "high", "low", "close", "volume", "change_pct"]
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"   # 3개월 수익률 계산에도 사용


def fetch_daily(symbol: str) -> list[dict]:
    """최근 1개월 일봉을 [{date, open, high, low, close, volume}, ...] 로 돌려준다 (오래된 순)."""
    url = CHART_URL.format(sym=urllib.parse.quote(symbol))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)

    result = (data.get("chart") or {}).get("result")
    if not result:
        err = (data.get("chart") or {}).get("error")
        raise RuntimeError(f"{symbol}: 데이터 없음 ({err})")
    r = result[0]
    tz = timezone(timedelta(seconds=r["meta"].get("gmtoffset", 0)))   # 거래소 현지 날짜 기준
    q = r["indicators"]["quote"][0]

    rows = []
    for i, ts in enumerate(r.get("timestamp", [])):
        close = q["close"][i]
        if close is None:          # 장중 미확정·휴장 값은 건너뜀
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d"),
            "open": round(q["open"][i], 2),
            "high": round(q["high"][i], 2),
            "low": round(q["low"][i], 2),
            "close": round(close, 2),
            "volume": int(q["volume"][i] or 0),
        })
    return rows


def load_csv() -> dict:
    store = {}
    if CSV_PATH.exists():
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                store[(row["ticker"], row["date"])] = row
    return store


def save_csv(store: dict) -> None:
    rows = sorted(store.values(), key=lambda r: (r["ticker"], r["date"]))
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:   # utf-8-sig: 엑셀에서 한글 안 깨짐
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def inject_into_game(out: dict) -> None:
    """index.html 의 MARKET_DATA 표시 사이를 최신 데이터로 바꿔 쓴다 (결과화면 '오늘의 시장 바이탈' 카드용).
    게임 페이지는 외부 사이트에 직접 접속할 수 없어서 데이터를 페이지 안에 넣어 둔다."""
    if not GAME_PATH.exists():
        return
    html = GAME_PATH.read_text(encoding="utf-8")
    start, end = html.find(MARK_START), html.find(MARK_END)
    if start < 0 or end < start:
        print(f"[건너뜀] {GAME_PATH.name} 에 MARKET_DATA 표시가 없어 게임 데이터는 그대로예요.", file=sys.stderr)
        return
    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    block = f'{MARK_START}\n<script id="market-data" type="application/json">{payload}</script>\n'
    GAME_PATH.write_text(html[:start] + block + html[end:], encoding="utf-8")
    print(f"게임 데이터 갱신: {GAME_PATH.name} (다시 게시해야 링크에 반영돼요)")


def main() -> int:
    store = load_csv()
    first_run = not store
    added = 0
    three_month = {}

    for sym, name in TICKERS.items():
        try:
            bars = fetch_daily(sym)
        except Exception as e:  # 네트워크 오류 등: 다른 종목은 계속 수집
            print(f"[실패] {name} ({sym}): {e}", file=sys.stderr)
            continue
        if not bars:
            continue

        # 지난 3개월 수익률: 3개월 전 첫 거래일 종가 → 가장 최근 종가, 주 단위 흐름도 함께
        first, last = bars[0], bars[-1]
        three_month[sym] = {
            "start_date": first["date"], "start_close": first["close"],
            "end_date": last["date"], "end_close": last["close"],
            "return_pct": round((last["close"] / first["close"] - 1) * 100, 2),
            "path": [{"date": b["date"], "close": b["close"]} for b in bars[::5]] + ([] if (len(bars) - 1) % 5 == 0 else [{"date": last["date"], "close": last["close"]}]),
        }

        # 직전 종가 대비 등락률 계산용
        prev_close = {bars[i]["date"]: bars[i - 1]["close"] for i in range(1, len(bars))}

        # 첫 실행이면 가장 최근 거래일 1개만, 이후에는 수집 시작일 이후 빠진 날을 채움
        existing = sorted(d for (t, d) in store if t == sym)
        start = existing[0] if existing else bars[-1]["date"]
        for b in bars:
            if b["date"] < start:
                continue
            key = (sym, b["date"])
            pc = prev_close.get(b["date"])
            row = {
                **{k: str(v) for k, v in b.items()},
                "ticker": sym,
                "name": name,
                "change_pct": f"{(b['close'] / pc - 1) * 100:.2f}" if pc else "",
            }
            if key not in store:
                added += 1
            store[key] = row   # 같은 날 재실행 시 최신 값으로 덮어씀

    if not store:
        print("수집된 데이터가 없습니다.", file=sys.stderr)
        return 1
    save_csv(store)

    # 게임용 JSON: 종목별 최근 TARGET_DAYS 거래일
    out = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "Yahoo Finance", "series": {}}
    for sym, name in TICKERS.items():
        rows = sorted((r for (t, _), r in store.items() if t == sym), key=lambda r: r["date"])[-TARGET_DAYS:]
        out["series"][sym] = {
            "name": name,
            "days": [{"date": r["date"], "close": float(r["close"]),
                      "change_pct": float(r["change_pct"]) if r["change_pct"] else None} for r in rows],
        }
        if sym in three_month:
            out["series"][sym]["three_month"] = three_month[sym]
    JSON_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    inject_into_game(out)

    # 터미널 요약
    print(f"{'첫 수집' if first_run else '수집'} 완료 · 새로 추가 {added}건 · {out['updated']}")
    print(f"{'종목':<10}{'날짜':<12}{'종가':>12}{'등락률':>9}")
    for sym, s in out["series"].items():
        for d in s["days"]:
            chg = "" if d["change_pct"] is None else f"{d['change_pct']:+.2f}%"
            print(f"{s['name']:<10}{d['date']:<12}{d['close']:>12,.2f}{chg:>9}")
    for sym, s in out["series"].items():
        tm = s.get("three_month")
        if tm:
            print(f"3개월 수익률 {s['name']}: {tm['start_date']} {tm['start_close']:,.2f} → {tm['end_date']} {tm['end_close']:,.2f}  {tm['return_pct']:+.2f}%")
    n = max(len(s["days"]) for s in out["series"].values())
    print(f"진행: {n}/{TARGET_DAYS} 거래일" + ("  → 수집 완료!" if n >= TARGET_DAYS else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
