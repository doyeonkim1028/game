"""매일 자동 게시 전에 안전 확인.

index.html 과 마지막으로 게시한 사본(.published/index.html)을 비교해서,
MARKET_DATA 블록(시세 데이터) 말고 다른 곳이 바뀌었으면 게시하지 않도록 알려준다.

  python publish_check.py           → 확인만 (종료 코드 0: 데이터만 바뀜, 1: 다른 곳도 바뀜, 2: 오류)
  python publish_check.py --save    → 게시가 끝난 뒤 현재 index.html 을 새 기준 사본으로 저장
"""
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE / "index.html"
SNAP = HERE / ".published" / "index.html"
BLOCK = re.compile(r"<!-- MARKET_DATA:START -->.*?<!-- MARKET_DATA:END -->", re.S)
DATA = re.compile(r'<script id="market-data" type="application/json">(.*?)</script>', re.S)


def strip(text: str) -> str:
    return BLOCK.sub("<!-- MARKET_DATA -->", text)


def main() -> int:
    if "--save" in sys.argv:
        SNAP.parent.mkdir(exist_ok=True)
        shutil.copyfile(LIVE, SNAP)
        print("기준 사본 저장:", SNAP)
        return 0
    html = LIVE.read_text(encoding="utf-8")
    m = DATA.search(html)
    try:
        data = json.loads(m.group(1).replace("<\\/", "</"))
        assert data["series"]["^KS11"]["days"] and data["series"]["^GSPC"]["days"]
    except Exception as e:
        print("오류: 시세 데이터 블록이 비었거나 깨졌어요:", e)
        return 2
    if not SNAP.exists():
        print("오류: 기준 사본(.published/index.html)이 없어요.")
        return 2
    if strip(html) != strip(SNAP.read_text(encoding="utf-8")):
        print("게시 중단: 시세 데이터 말고 다른 내용도 바뀌었어요. 사람이 확인한 뒤 게시해야 해요.")
        return 1
    print(f"확인 완료: 시세 데이터만 바뀜 (업데이트 {data['updated']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
