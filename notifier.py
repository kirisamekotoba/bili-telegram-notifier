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
    "User-Agent": "Mozilla/5.0 (Actions; BiliDyn/2.0)",
    "Accept": "application/json, text/xml, */*;q=0.1",
    "Referer": "https://t.bilibili.com/",
})

API_POLYMER = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}"
RSS_ORIGIN  = "https://rsshub.app/bilibili/user/dynamic/{uid}"  # 兜底

def log(msg: str):
    print(msg, flush=True)

def load_state():
    # 兼容旧结构：如果原来是 {"seen": {...}} 也能读
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text("utf-8"))
            if "last_dyn" in data:
                return data
            elif "seen" in data:  # 旧版迁移成 last_dyn
                # 取每个 uid 已见列表的最后一项作为 last_dyn
                last_dyn = {k: (v[-1] if isinstance(v, list) and v else None) for k, v in data["seen"].items()}
                return {"last_dyn": last_dyn}
        except Exception as e:
            log(f"[WARN] load_state failed: {e}")
    return {"last_dyn": {}}  # { uid: last_dynamic_id }

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

def format_msg(uid: str, title: str, url: Optional[str]):
    head = f"👀 <b>UP {uid} 有新动态</b>"
    body = f"📝 {title}" if title else "📝 新动态"
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

def fetch_polymer(uid: str) -> List[Tuple[str,str,Optional[str]]]:
    """
    只抓“动态/说说”，返回按时间从新到旧的列表：
    [(dynamic_id_str, title_text, link)]
    """
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
            title = desc.get("text") or "B站动态"
            link  = f"https://t.bilibili.com/{id_str}"
            results.append((id_str, title, link))
        log(f"[INFO] polymer items for {uid}: {len(results)}")
        return results  # polymer 默认已是新→旧
    except Exception as e:
        log(f"[WARN] polymer failed for {uid}: {e}")
        return []

def fetch_rss(uid: str) -> List[Tuple[str,str,Optional[str]]]:
    """
    RSS 兜底，返回新→旧（按 item 顺序近似）
    用 title+link 做一个稳定 hash 作为伪ID并存入 id_str，以便去重。
    """
    try:
        r = retry_get(RSS_ORIGIN.format(uid=uid), tries=2, backoff_base=1.2)
        text = r.text
        blocks = text.split("<item>")[1:10]
        results = []
        for block in blocks:
            title = "B站动态"
            if "<title>" in block and "</title>" in block:
                title = block.split("<title>",1)[1].split("</title>",1)[0].strip()
            link = None
            if "<link>" in block and "</link>" in block:
                link = block.split("<link>",1)[1].split("</link>",1)[0].strip()
            raw = (title or "") + "|" + (link or "") + "|" + block[:200]
            id_str = hashlib.md5(raw.encode("utf-8")).hexdigest()
            results.append((id_str, title, link))
        log(f"[INFO] rss items for {uid}: {len(results)}")
        return results
    except Exception as e:
        log(f"[WARN] rss failed for {uid}: {e}")
        return []

def main():
    state = load_state()
    last_dyn = state.get("last_dyn", {})

    pushed_any = False

    for uid in BILI_UIDS:
        log(f"[INFO] Fetching UID {uid}…")

        # 先 polymer，再 RSS 兜底
        items = fetch_polymer(uid)
        if not items:
            items = fetch_rss(uid)

        if not items:
            log(f"[INFO] no items for {uid}")
            continue

        # 只推送“最新一条 且 之前没推送过”的那条
        # items 已是新→旧，取第一条与 last_dyn[uid] 比较
        newest_id, newest_title, newest_link = items[0]
        last_id = last_dyn.get(uid)

        if newest_id and newest_id != last_id:
            # 推送这 1 条
            try:
                send_telegram(format_msg(uid, newest_title, newest_link))
                log(f"[OK] pushed newest {uid}/{newest_id}")
                last_dyn[uid] = newest_id
                pushed_any = True
            except Exception as e:
                log(f"[ERROR] push failed for {uid}/{newest_id}: {e}")
        else:
            log(f"[INFO] newest unchanged for {uid} (newest={newest_id}, last={last_id})")

    # 保存 last_dyn
    state["last_dyn"] = last_dyn
    save_state(state)

    if not pushed_any:
        log("[INFO] No new updates to push")

if __name__ == "__main__":
    main()
