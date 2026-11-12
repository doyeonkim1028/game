"""야후 파이낸스에서 게임에 쓰는 실제 상품(ETF) 시세를 매일 수집한다.

상품 (게임의 자산 4종 중 3종, 예금은 게임 안의 한국은행 금리 표를 씀)
  - 국내주식: KODEX 200 (069500.KS) — 코스피200 추종, 거래량 1위 국내 ETF
  - 해외주식: SPY — 세계 최대 S&P 500 ETF, 원/달러 환율(KRW=X)을 곱해 원화로 환산
  - 채권:     KODEX 국고채3년 (114260.KS)

- 외부 패키지 없이 파이썬 표준 라이브러리만 사용 (pandas/yfinance 불필요)
- 하루 한 번 실행하면 market_data.csv 에 그날 데이터가 쌓인다 (같은 날 여러 번 실행해도 중복 없음)
- 수익률은 배당(분배금)을 반영한 수정주가(adjclose)로 계산한다
- 실행할 때마다 market_data.json 을 다시 만들고, index.html 결과화면 카드용 데이터도 바꿔 쓴다
- 첫 실행일부터 TARGET_DAYS 거래일이 모이면 "수집 완료"를 알려준다

사용법:  python collect_market.py
"""
import bisect
import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

INSTRUMENTS = {
    "069500.KS": {"name": "KODEX 200", "role": "국내주식", "fx": False},
    "SPY": {"name": "SPY", "role": "해외주식", "fx": True},
    "114260.KS": {"name": "KODEX 국고채3년", "role": "채권", "fx": False},
}
FX_SYMBOL = "KRW=X"   # 원/달러 환율
TARGET_DAYS = 10
HERE = Path(__file__).resolve().parent
CSV_PATH = HERE / "market_data.csv"
JSON_PATH = HERE / "market_data.json"
GAME_PATH = HERE / "index.html"
MARK_START, MARK_END = "<!-- MARKET_DATA:START -->", "<!-- MARKET_DATA:END -->"
FIELDS = ["date", "ticker", "name", "open", "high", "low", "close", "volume", "change_pct", "usd_krw", "close_krw"]
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"   # 3개월 수익률 계산에도 사용


def fetch_daily(symbol: str) -> list[dict]:
    """최근 3개월 일봉을 [{date, open, high, low, close, adj, volume}, ...] 로 돌려준다 (오래된 순)."""
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
    adj = ((r["indicators"].get("adjclose") or [{}])[0].get("adjclose")) or q["close"]

    rows = []
    for i, ts in enumerate(r.get("timestamp", [])):
        close = q["close"][i]
        if close is None:          # 장중 미확정·휴장 값은 건너뜀
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d"),
            "open": round(q["open"][i] or close, 4),
            "high": round(q["high"][i] or close, 4),
            "low": round(q["low"][i] or close, 4),
            "close": round(close, 4),
            "adj": adj[i] if adj[i] is not None else close,
            "volume": int(q["volume"][i] or 0),
        })
    return rows


def fx_on(fx: list[dict], date: str) -> float:
    """date 당일(없으면 직전 거래일) 원/달러 환율."""
    dates = [b["date"] for b in fx]
    i = max(0, bisect.bisect_right(dates, date) - 1)
    return fx[i]["close"]


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
        w = csv.DictWriter(f, fieldnames=FIELDS, restval="", extrasaction="ignore")
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
    first_run = not any(t in INSTRUMENTS for (t, _) in store)
    added = 0
    three_month = {}

    try:
        fx = fetch_daily(FX_SYMBOL)
    except Exception as e:
        print(f"[실패] 원/달러 환율 ({FX_SYMBOL}): {e}", file=sys.stderr)
        fx = []

    for sym, info in INSTRUMENTS.items():
        name = info["name"]
        try:
            bars = fetch_daily(sym)
        except Exception as e:  # 네트워크 오류 등: 다른 종목은 계속 수집
            print(f"[실패] {name} ({sym}): {e}", file=sys.stderr)
            continue
        if not bars:
            continue
        if info["fx"]:
            if not fx:
                print(f"[건너뜀] {name}: 환율이 없어 원화로 환산할 수 없어요.", file=sys.stderr)
                continue
            for b in bars:   # 원화 환산 (가격·수정주가 모두)
                b["usd_krw"] = fx_on(fx, b["date"])
                b["close_krw"] = b["close"] * b["usd_krw"]
                b["adj_krw"] = b["adj"] * b["usd_krw"]
        else:
            for b in bars:
                b["close_krw"], b["adj_krw"] = b["close"], b["adj"]

        # 지난 3개월 수익률 (배당 포함, 원화 기준): 3개월 전 첫 거래일 → 가장 최근 거래일
        first, last = bars[0], bars[-1]
        three_month[sym] = {
            "start_date": first["date"], "end_date": last["date"],
            "return_pct": round((last["adj_krw"] / first["adj_krw"] - 1) * 100, 2),
        }

        # 직전 거래일 대비 등락률 (원화 가격 기준)
        prev = {bars[i]["date"]: bars[i - 1]["close_krw"] for i in range(1, len(bars))}

        # 첫 실행이면 가장 최근 거래일 1개만, 이후에는 수집 시작일 이후 빠진 날을 채움
        existing = sorted(d for (t, d) in store if t == sym)
        start = existing[0] if existing else last["date"]
        for b in bars:
            if b["date"] < start:
                continue
            key = (sym, b["date"])
            pc = prev.get(b["date"])
            row = {
                "date": b["date"], "ticker": sym, "name": name,
                "open": str(b["open"]), "high": str(b["high"]), "low": str(b["low"]),
                "close": str(b["close"]), "volume": str(b["volume"]),
                "change_pct": f"{(b['close_krw'] / pc - 1) * 100:.2f}" if pc else "",
                "usd_krw": f"{b['usd_krw']:.2f}" if info["fx"] else "",
                "close_krw": f"{b['close_krw']:.0f}",
            }
            if key not in store:
                added += 1
            store[key] = row   # 같은 날 재실행 시 최신 값으로 덮어씀

    if not any(t in INSTRUMENTS for (t, _) in store):
        print("수집된 데이터가 없습니다.", file=sys.stderr)
        return 1
    save_csv(store)

    # 게임용 JSON: 상품별 최근 TARGET_DAYS 거래일 + 3개월 수익률
    out = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "Yahoo Finance", "series": {}}
    for sym, info in INSTRUMENTS.items():
        rows = sorted((r for (t, _), r in store.items() if t == sym), key=lambda r: r["date"])[-TARGET_DAYS:]
        if not rows:
            continue
        s = {
            "name": info["name"], "role": info["role"],
            "days": [{"date": r["date"], "close": float(r["close_krw"] or r["close"]),
                      **({"usd": float(r["close"])} if info["fx"] else {}),
                      "change_pct": float(r["change_pct"]) if r["change_pct"] else None} for r in rows],
        }
        if sym in three_month:
            s["three_month"] = three_month[sym]
        out["series"][sym] = s
    JSON_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    inject_into_game(out)

    # 터미널 요약
    print(f"{'첫 수집' if first_run else '수집'} 완료 · 새로 추가 {added}건 · {out['updated']}")
    print(f"{'상품':<16}{'날짜':<12}{'가격(원)':>14}{'등락률':>9}")
    for s in out["series"].values():
        for d in s["days"]:
            chg = "" if d["change_pct"] is None else f"{d['change_pct']:+.2f}%"
            usd = f"  (${d['usd']:,.2f})" if "usd" in d else ""
            print(f"{s['name']:<16}{d['date']:<12}{d['close']:>14,.0f}{chg:>9}{usd}")
    for s in out["series"].values():
        tm = s.get("three_month")
        if tm:
            print(f"3개월 수익률 {s['name']}: {tm['start_date']} → {tm['end_date']}  {tm['return_pct']:+.2f}% (배당 포함, 원화)")
    n = max(len(s["days"]) for s in out["series"].values())
    print(f"진행: {n}/{TARGET_DAYS} 거래일" + ("  → 수집 완료!" if n >= TARGET_DAYS else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
