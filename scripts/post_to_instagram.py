"""
Instagram Graph API 自動投稿スクリプト

content/posts/ 配下のMarkdownを読み取り、未投稿の記事をInstagramに投稿する。
投稿成功後、front matterのposted_atを更新してgit commit する。

必要な環境変数:
  INSTAGRAM_ACCESS_TOKEN  — Graph API長期アクセストークン
  INSTAGRAM_BUSINESS_ID   — InstagramビジネスアカウントID
"""

import os
import re
import sys
import json
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
GRAPH_API = "https://graph.facebook.com/v21.0"
PAGES_BASE = "https://tanishi1234.github.io/kawagoe"
POSTS_DIR = Path(__file__).resolve().parent.parent / "content" / "posts"
POLL_INTERVAL = 5       # コンテナステータスのポーリング間隔(秒)
POLL_TIMEOUT = 300      # タイムアウト(秒)


def get_env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        print(f"ERROR: 環境変数 {key} が設定されていません")
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# Front matter パーサー
# ---------------------------------------------------------------------------
FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_front_matter(text: str) -> tuple[dict, str]:
    """簡易YAMLパーサー。front matterをdictで返し、本文も返す。"""
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text

    fm_raw = m.group(1)
    body = text[m.end():]
    fm = {}
    current_key = None
    current_list = None

    for line in fm_raw.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # リスト項目
        if stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
                fm[current_key] = current_list
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue

        # key: value
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            current_key = key
            current_list = None
            if val:
                fm[key] = val
            else:
                fm[key] = ""
    return fm, body


def serialize_front_matter(fm: dict, body: str) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f'  - "{item}"')
        else:
            lines.append(f'{k}: "{v}"' if v else f"{k}:")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body


# ---------------------------------------------------------------------------
# Graph API ヘルパー
# ---------------------------------------------------------------------------
def graph_post(endpoint: str, params: dict) -> dict:
    url = f"{GRAPH_API}/{endpoint}"
    data = urlencode(params).encode()
    req = Request(url, data=data, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        err_body = e.read().decode()
        print(f"API ERROR {e.code}: {err_body}")
        raise


def graph_get(endpoint: str, params: dict) -> dict:
    qs = urlencode(params)
    url = f"{GRAPH_API}/{endpoint}?{qs}"
    req = Request(url, method="GET")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def wait_for_container(ig_id: str, container_id: str, token: str) -> bool:
    """コンテナがFINISHEDになるまでポーリング。"""
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        result = graph_get(container_id, {
            "fields": "status_code",
            "access_token": token,
        })
        status = result.get("status_code", "")
        print(f"  container {container_id}: {status}")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            print(f"  ERROR: コンテナ処理に失敗しました")
            return False
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    print(f"  TIMEOUT: {POLL_TIMEOUT}秒経過")
    return False


# ---------------------------------------------------------------------------
# 投稿ロジック
# ---------------------------------------------------------------------------
def image_url(filename: str) -> str:
    return f"{PAGES_BASE}/images/posts/{filename}"


def post_single_image(ig_id: str, token: str, img: str, caption: str) -> str | None:
    """画像1枚の投稿。成功時にメディアIDを返す。"""
    url = image_url(img)
    print(f"  IMAGE投稿: {url}")

    # 1. コンテナ作成
    container = graph_post(f"{ig_id}/media", {
        "image_url": url,
        "caption": caption,
        "access_token": token,
    })
    cid = container["id"]

    if not wait_for_container(ig_id, cid, token):
        return None

    # 2. 公開
    result = graph_post(f"{ig_id}/media_publish", {
        "creation_id": cid,
        "access_token": token,
    })
    return result.get("id")


def post_carousel(ig_id: str, token: str, images: list[str], caption: str) -> str | None:
    """カルーセル投稿。"""
    print(f"  CAROUSEL投稿: {len(images)}枚")

    # 1. 子コンテナ作成
    child_ids = []
    for img in images:
        url = image_url(img)
        print(f"    子コンテナ: {url}")
        child = graph_post(f"{ig_id}/media", {
            "image_url": url,
            "is_carousel_item": "true",
            "access_token": token,
        })
        child_ids.append(child["id"])

    # 子コンテナの完了を待つ
    for cid in child_ids:
        if not wait_for_container(ig_id, cid, token):
            return None

    # 2. カルーセルコンテナ作成
    carousel = graph_post(f"{ig_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
        "access_token": token,
    })
    car_id = carousel["id"]

    if not wait_for_container(ig_id, car_id, token):
        return None

    # 3. 公開
    result = graph_post(f"{ig_id}/media_publish", {
        "creation_id": car_id,
        "access_token": token,
    })
    return result.get("id")


def post_reel(ig_id: str, token: str, video: str, caption: str) -> str | None:
    """リール投稿。"""
    url = image_url(video)
    print(f"  REELS投稿: {url}")

    # 1. コンテナ作成
    container = graph_post(f"{ig_id}/media", {
        "media_type": "REELS",
        "video_url": url,
        "caption": caption,
        "access_token": token,
    })
    cid = container["id"]

    if not wait_for_container(ig_id, cid, token):
        return None

    # 2. 公開
    result = graph_post(f"{ig_id}/media_publish", {
        "creation_id": cid,
        "access_token": token,
    })
    return result.get("id")


# ---------------------------------------------------------------------------
# Git操作
# ---------------------------------------------------------------------------
def git_commit_file(filepath: Path, message: str):
    subprocess.run(["git", "add", str(filepath)], check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True,
    )


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def process_post(filepath: Path, ig_id: str, token: str) -> bool:
    """1つのMarkdownファイルを処理。投稿成功ならTrueを返す。"""
    text = filepath.read_text(encoding="utf-8")
    fm, body = parse_front_matter(text)

    # 投稿済みチェック
    if fm.get("posted_at"):
        return False

    caption = fm.get("instagram_caption", "")
    if not caption:
        print(f"SKIP: {filepath.name} — instagram_captionが未設定")
        return False

    images = fm.get("images", [])
    if isinstance(images, str):
        images = [images] if images else []

    if not images:
        print(f"SKIP: {filepath.name} — imagesが未設定")
        return False

    # scheduled_atチェック（未来日なら投稿しない）
    scheduled = fm.get("scheduled_at", "")
    if scheduled:
        try:
            sched_dt = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
            if sched_dt > datetime.now(timezone.utc):
                print(f"SKIP: {filepath.name} — 投稿予定: {scheduled}")
                return False
        except ValueError:
            pass

    print(f"\n{'='*60}")
    print(f"投稿: {filepath.name}")
    print(f"  caption: {caption[:60]}...")
    print(f"  images: {images}")

    # 投稿タイプ判定
    media_id = None
    if len(images) == 1:
        filename = images[0]
        if filename.lower().endswith(".mp4"):
            media_id = post_reel(ig_id, token, filename, caption)
        else:
            media_id = post_single_image(ig_id, token, filename, caption)
    else:
        # 混在チェック: mp4が含まれる場合はエラー
        has_video = any(f.lower().endswith(".mp4") for f in images)
        if has_video:
            print(f"  ERROR: カルーセルに動画は含められません（動画は1本でREELS投稿してください）")
            return False
        media_id = post_carousel(ig_id, token, images, caption)

    if not media_id:
        print(f"  FAILED: 投稿に失敗しました")
        return False

    print(f"  SUCCESS: media_id={media_id}")

    # front matter更新
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm["posted_at"] = now
    fm["instagram_media_id"] = media_id
    updated = serialize_front_matter(fm, body)
    filepath.write_text(updated, encoding="utf-8")

    # git commit
    git_commit_file(filepath, f"instagram: {filepath.stem} を投稿 ({media_id})")
    print(f"  COMMITTED: posted_at={now}")
    return True


def main():
    token = get_env("INSTAGRAM_ACCESS_TOKEN")
    ig_id = get_env("INSTAGRAM_BUSINESS_ID")

    if not POSTS_DIR.exists():
        print(f"ERROR: {POSTS_DIR} が存在しません")
        sys.exit(1)

    md_files = sorted(POSTS_DIR.glob("*.md"))
    if not md_files:
        print("投稿対象のMarkdownファイルがありません")
        return

    print(f"対象ファイル: {len(md_files)}件")
    posted = 0

    for f in md_files:
        try:
            if process_post(f, ig_id, token):
                posted += 1
        except Exception as e:
            print(f"  ERROR: {f.name} — {e}")

    print(f"\n{'='*60}")
    print(f"完了: {posted}件 投稿しました")

    if posted > 0:
        subprocess.run(["git", "push"], check=True)
        print("git push 完了")


if __name__ == "__main__":
    main()
