"""
Instagram Graph API キャプション一括取得スクリプト

投稿済みのリール/フィードのキャプションをまとめて取得し、
map/index.html の SPOTS に貼り付けられる雛形を出力する。

必要な環境変数:
  INSTAGRAM_ACCESS_TOKEN  — Graph API長期アクセストークン
  INSTAGRAM_BUSINESS_ID   — InstagramビジネスアカウントID

使い方:
  python3 scripts/fetch_captions.py              # 全件を一覧表示
  python3 scripts/fetch_captions.py --reels      # リールのみ
  python3 scripts/fetch_captions.py --json       # JSONで出力
  python3 scripts/fetch_captions.py --skeleton   # SPOTS雛形を出力
"""

import os
import sys
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
GRAPH_API = "https://graph.facebook.com/v21.0"
PAGE_LIMIT = 100        # 1ページあたりの取得件数
FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"


def get_env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        print(f"ERROR: 環境変数 {key} が設定されていません")
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# Graph API
# ---------------------------------------------------------------------------
def graph_get(endpoint: str, params: dict) -> dict:
    qs = urlencode(params)
    url = f"{GRAPH_API}/{endpoint}?{qs}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"API ERROR {e.code}: {e.read().decode()}")
        raise


def fetch_all_media(ig_id: str, token: str) -> list:
    """ページネーションを辿って全メディアを取得する。"""
    items = []
    params = {"fields": FIELDS, "limit": PAGE_LIMIT, "access_token": token}
    endpoint = f"{ig_id}/media"

    while True:
        data = graph_get(endpoint, params)
        items.extend(data.get("data", []))

        after = data.get("paging", {}).get("cursors", {}).get("after")
        if not after or not data.get("paging", {}).get("next"):
            break
        params["after"] = after

    return items


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def print_list(items: list) -> None:
    for i, m in enumerate(items, 1):
        caption = (m.get("caption") or "（キャプションなし）").strip()
        kind = m.get("media_product_type") or m.get("media_type") or "-"
        date = (m.get("timestamp") or "")[:10]
        print(f"\n{'=' * 60}")
        print(f"[{i}] {date}  {kind}  "
              f"♥{m.get('like_count', '-')}  💬{m.get('comments_count', '-')}")
        print(m.get("permalink", ""))
        print("-" * 60)
        print(caption)


def print_skeleton(items: list) -> None:
    """map/index.html の SPOTS に貼れる雛形を出力する。"""
    print("/* ↓ map/index.html の SPOTS に貼り付けて、各項目を埋めてください */")
    for m in items:
        caption = (m.get("caption") or "").strip()
        first = caption.split("\n")[0][:40] if caption else ""
        print(f'''  {{ name:"{first}", area:"", genre:"", level:"穴場",
    desc:"", price:"", tip:"",
    reel:"{m.get("permalink", "")}" }},''')


def main() -> None:
    token = get_env("INSTAGRAM_ACCESS_TOKEN")
    ig_id = get_env("INSTAGRAM_BUSINESS_ID")

    items = fetch_all_media(ig_id, token)

    if "--reels" in sys.argv:
        items = [m for m in items if m.get("media_product_type") == "REELS"]

    print(f"取得件数: {len(items)} 件", file=sys.stderr)

    if "--json" in sys.argv:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    elif "--skeleton" in sys.argv:
        print_skeleton(items)
    else:
        print_list(items)


if __name__ == "__main__":
    main()
