import os, json, time, hashlib
from pathlib import Path
import requests

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]
BILI_UIDS    = [u.strip() for u in os.environ["BILI_UIDS"].split(",") if u.strip()]

STATE_FILE = Path("state.json")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Actions; BiliWatch/1.1)",
    "Accept": "application/json, text/xml, */*;q=0.1",
    "Referer": "https://t.bilibili.com/",
})

API_FMT = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}"
RSS_FMT = "https://rsshub.app/bilibili/user/dynamic/{uid}"

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

def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def send_telegram(text: str, disable_preview=False):
    api = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    r = SESSION.post(api, timeout=20, json={
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    })
    try:
        r.raise_for_status()
    except Exception as e:
        log(f"[ERROR] telegram send failed: {e} | resp={r.text[:200]}")
        raise
    return r.json()

def fetch_polymer(uid: str):
    """返回 [(dynamic_id_str, title, url)]"""
    url = API_FMT.format(uid=uid)
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    items = (data.get("data") or {}).get("items") or []
    results = []
    for it in items:
        id_str = str(it.get("id_str") or it.get("id") or "")
        if not id_str:
            continue
        title = None
        # 尽量从 desc 里拿一段文本当标题
        modules = it.get("modules") or {}
        desc = (modules.get("module_dynamic") or {}).get("desc") or {}
        title = desc.get("text") or "B站动态更新"
        link = f"https://t.bilibili.com/{id_str}"
        results.append((id_str, title, link))
    log(f"[INFO] polymer items for {uid}: {len(results)}")
    return results

def fetch_rsshub(uid: str):
    """RSSHub 回退：解析 XML 前几条，用内容做 hash 作为伪ID"""
    url = RSS_FMT.format(uid=uid)
    r = SESSION.get(url, timeout=20)
    r.raise_for_status()
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
        pid = md5((title or "") + (link or "") + block[:200])
        results.append((pid, title, link))
    log(f"[INFO] rsshub items for {uid}: {len(results)}")
    return results

def format_msg(uid: str, title: str, url: str|None):
    head = f"👀 <b>UP {uid} 有新动态</b>"
    body = f"📝 {title}" if title else "📝 新动态"
    tail = f"\n🔗 {url}" if url else ""
    return f"{head}\n{body}{tail}"

def main():
    # 自检：每次运行先发一条（方便你判断是“抓取失败”还是“发送失败”）
    try:
        send_telegram("🔔 Bili Notifier 自检：工作流已启动。")
    except Exception:
        # 如果连自检都失败，后续也发不出，直接返回使日志更干净
        return

    state = load_state()
    seen = state.get("seen", {})

    any_new = False

    for uid in BILI_UIDS:
        log(f"[INFO] Fetching UID {uid}…")
        items = []
        # 先 polymer，再回退 RSSHub
        try:
            items = fetch_polymer(uid)
        except Exception as e:
            log(f"[WARN] polymer failed for {uid}: {e}")

        if not items:
            try:
                items = fetch_rsshub(uid)
            except Exception as e:
                log(f"[ERROR] rsshub failed for {uid}: {e}")

        already = set(seen.get(uid, []))
        new_items = [it for it in items if it[0] not in already]
        log(f"[INFO] new items for {uid}: {len(new_items)}")

        # 推送最近 1~3 条，按时间正序发
        for id_str, title, link in new_items[:3][::-1]:
            msg = format_msg(uid, title, link)
            try:
                send_telegram(msg, disable_preview=False)
                log(f"[OK] pushed {uid}/{id_str}")
                already.add(id_str)
                any_new = True
                time.sleep(0.5)
            except Exception as e:
                log(f"[ERROR] push failed for {uid}/{id_str}: {e}")

        seen[uid] = list(already)[-100:]

    state["seen"] = seen
    save_state(state)

    if not any_new:
        log("[INFO] No new updates")

if __name__ == "__main__":
    main()
