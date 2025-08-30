import os, json, time, hashlib, random
from pathlib import Path
from typing import List, Tuple, Optional
import requests

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]
BILI_UIDS    = [u.strip() for u in os.environ["BILI_UIDS"].split(",") if u.strip()]
STATE_FILE = Path("state.json")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Actions; BiliWatch/1.2)",
    "Accept": "application/json, text/xml, */*;q=0.1",
    "Referer": "https://space.bilibili.com/",
})

API_POLYMER = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}"
API_VIDEOS  = "https://api.bilibili.com/x/space/arc/search?mid={uid}&ps=5&pn=1&order=pubdate"
API_ARTICLE = "https://api.bilibili.com/x/space/article?mid={uid}&pn=1&ps=5&sort=publish_time"
RSS_ORIGIN  = "https://rsshub.app/bilibili/user/dynamic/{uid}"  # 作为兜底

def log(msg: str):
    print(msg, flush=True)

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except Exception as e:
            log(f"[WARN] load_state failed: {e}")
    return {"seen": {}}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

def send_telegram(text: str, disable_preview=False):
    api = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    r = SESSION.post(api, timeout=20, json={
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    })
    r.raise_for_status()
    return r.json()

def format_msg(uid: str, title: str, url: Optional[str], tag: str):
    head = f"👀 <b>UP {uid} 有新{tag}</b>"
    body = f"📝 {title}" if title else "📝 新内容"
    tail = f"\n🔗 {url}" if url else ""
    return f"{head}\n{body}{tail}"

def retry_get(url, tries=3, backoff_base=0.8):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=15)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            sleep = backoff_base * (2 ** i) + random.random() * 0.3
            log(f"[WARN] GET {url} failed (try {i+1}/{tries}): {e} -> sleep {sleep:.1f}s")
            time.sleep(sleep)
    raise last

# ----- 抓 polymer 动态（说说） -----
def fetch_polymer(uid: str) -> List[Tuple[str,str,Optional[str]]]:
    try:
        r = retry_get(API_POLYMER.format(uid=uid), tries=2)
        data = r.json()
        items = (data.get("data") or {}).get("items") or []
        results = []
        for it in items:
            id_str = str(it.get("id_str") or it.get("id") or "")
            if not id_str:
                continue
            modules = it.get("modules") or {}
            desc = (modules.get("module_dynamic") or {}).get("desc") or {}
            title = desc.get("text") or "B站动态（说说）"
            link  = f"https://t.bilibili.com/{id_str}"
            results.append((f"dyn:{id_str}", title, link))
        log(f"[INFO] polymer items for {uid}: {len(results)}")
        return results
    except Exception as e:
        log(f"[WARN] polymer failed for {uid}: {e}")
        return []

# ----- 抓“最新视频” -----
def fetch_videos(uid: str) -> List[Tuple[str,str,Optional[str]]]:
    try:
        r = retry_get(API_VIDEOS.format(uid=uid), tries=2)
        data = r.json()
        vlist = (((data.get("data") or {}).get("list") or {}).get("vlist") or [])
        results = []
        for v in vlist:
            bvid = v.get("bvid")
            title = (v.get("title") or "").strip() or "新视频"
            if not bvid: 
                # 旧字段名兼容
                bvid = v.get("bvid") or v.get("bvid_")
            if not bvid:
                # 极少数返回没有 bvid 的情况，跳过
                continue
            link = f"https://www.bilibili.com/video/{bvid}"
            results.append((f"video:{bvid}", title, link))
        log(f"[INFO] videos for {uid}: {len(results)}")
        return results
    except Exception as e:
        log(f"[WARN] videos failed for {uid}: {e}")
        return []

# ----- 抓“专栏文章” -----
def fetch_articles(uid: str) -> List[Tuple[str,str,Optional[str]]]:
    try:
        r = retry_get(API_ARTICLE.format(uid=uid), tries=2)
        data = r.json()
        arts = (data.get("data") or {}).get("articles") or []
        results = []
        for a in arts:
            aid = a.get("id")
            title = (a.get("title") or "").strip() or "新专栏"
            if not aid:
                continue
            link = f"https://www.bilibili.com/read/cv{aid}"
            results.append((f"article:{aid}", title, link))
        log(f"[INFO] articles for {uid}: {len(results)}")
        return results
    except Exception as e:
        log(f"[WARN] articles failed for {uid}: {e}")
        return []

# ----- RSS 兜底（依然尝试，但失败不阻断） -----
def fetch_rss(uid: str) -> List[Tuple[str,str,Optional[str]]]:
    try:
        r = retry_get(RSS_ORIGIN.format(uid=uid), tries=2, backoff_base=1.2)
        text = r.text
        blocks = text.split("<item>")[1:6]
        results = []
        for block in blocks:
            title = "B站动态更新"
            if "<title>" in block and "</title>" in block:
                title = block.split("<title>",1)[1].split("</title>",1)[0].strip()
            link = None
            if "<link>" in block and "</link>" in block:
                link = block.split("<link>",1)[1].split("</link>",1)[0].strip()
            pid = hashlib.md5((title or "" + (link or "") + block[:200]).encode("utf-8")).hexdigest()
            results.append((f"rss:{pid}", title, link))
        log(f"[INFO] rss items for {uid}: {len(results)}")
        return results
    except Exception as e:
        log(f"[WARN] rss failed for {uid}: {e}")
        return []

def main():
    # 自检：确保 TG 通路正常
    try:
        send_telegram("🔔 Bili Notifier 自检：工作流已启动。")
    except Exception as e:
        log(f"[ERROR] selfcheck telegram failed: {e}")
        return

    state = load_state()
    seen = state.get("seen", {})

    any_new = False

    for uid in BILI_UIDS:
        log(f"[INFO] Fetching UID {uid}…")

        # 按优先级合并不同来源：视频 > 专栏 > 动态 > RSS
        merged: List[Tuple[str,str,Optional[str],str]] = []
        for fetch_fn, tag in [
            (fetch_videos,  "视频"),
            (fetch_articles,"专栏"),
            (fetch_polymer, "动态"),
            (fetch_rss,     "RSS"),
        ]:
            try:
                items = fetch_fn(uid)
                merged.extend([(iid, title, link, tag) for (iid, title, link) in items])
            except Exception as e:
                log(f"[WARN] {tag} fetch error for {uid}: {e}")

        # 去重（以 id 为准，保留第一次出现的来源）
        unique = []
        ids = set()
        for iid, title, link, tag in merged:
            if iid in ids:
                continue
            ids.add(iid)
            unique.append((iid, title, link, tag))

        already = set(seen.get(uid, []))
        new_items = [it for it in unique if it[0] not in already]
        log(f"[INFO] new items for {uid}: {len(new_items)}")

        # 推送最近 1~3 条
        for iid, title, link, tag in new_items[:3][::-1]:
            msg = format_msg(uid, title, link, tag)
            try:
                send_telegram(msg, disable_preview=False)
                log(f"[OK] pushed {uid}/{iid} ({tag})")
                already.add(iid)
                any_new = True
                time.sleep(0.5)
            except Exception as e:
                log(f"[ERROR] push failed for {uid}/{iid}: {e}")

        seen[uid] = list(already)[-150:]

    state["seen"] = seen
    save_state(state)

    if not any_new:
        log("[INFO] No new updates")

if __name__ == "__main__":
    main()
