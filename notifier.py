import os, json, time, random
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import requests

# ====== 环境变量 ======
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]

# 以逗号分隔的房间号列表；例如： "22966160,12345"
BILI_ROOMS   = [r.strip() for r in os.environ.get("BILI_ROOMS", "").split(",") if r.strip()]

STATE_FILE = Path("state.json")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Actions; BiliLiveRoom/1.0)",
    "Accept": "application/json, */*;q=0.1",
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
})

# 官方房间信息接口（无需登录）：可批量查询
# 文档行为：room_ids 用逗号分隔；req_biz 传 "video" 即可
API_ROOM_BATCH = "https://api.live.bilibili.com/xlive/web-room/v1/index/getRoomBaseInfo?room_ids={room_ids}&req_biz=video"

def log(msg: str): print(msg, flush=True)

def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text("utf-8"))
            # 结构：{"live_status_by_room": {room_id: 0/1}, "title_by_room": {room_id: "..."}}
            if "live_status_by_room" in data:
                return data
        except Exception as e:
            log(f"[WARN] load_state failed: {e}")
    return {"live_status_by_room": {}, "title_by_room": {}}

def save_state(state: Dict):
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

def format_live_on(room_id: str, title: Optional[str]):
    url = f"https://live.bilibili.com/{room_id}"
    head = f"🟢 <b>直播间 {room_id} 开播</b>"
    body = f"🎯 {title}" if title else "🎯 直播开始"
    return f"{head}\n{body}\n🔗 {url}"

def format_live_off(room_id: str, title: Optional[str]):
    url = f"https://live.bilibili.com/{room_id}"
    head = f"⚪ <b>直播间 {room_id} 下播</b>"
    if title:
        return f"{head}\n📝 {title}\n🔗 {url}"
    return f"{head}\n🔗 {url}"

def fetch_rooms_info(room_ids: List[str]) -> Dict[str, Tuple[int, str]]:
    """
    返回 {room_id: (live_status, title)} ；live_status: 0 未开播 / 1 开播
    """
    info: Dict[str, Tuple[int, str]] = {}
    if not room_ids:
        return info
    # 分批（接口支持最多 50～100 个；我们通常很少）
    batch = ",".join(room_ids)
    url = API_ROOM_BATCH.format(room_ids=batch)
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    j = r.json()
    data = (j.get("data") or {}).get("room_info_list") or []
    for it in data:
        rid = str(it.get("room_id") or it.get("roomid") or "")
        if not rid:
            continue
        status = int(it.get("live_status") or 0)  # 0 / 1
        title  = (it.get("title") or "").strip()
        info[rid] = (status, title)
    log(f"[INFO] fetched {len(info)}/{len(room_ids)} room infos")
    return info

def main():
    # 从 Secrets 中拿房间号
    rooms = BILI_ROOMS
    if not rooms:
        log("[ERROR] No BILI_ROOMS provided (comma-separated room ids).")
        return

    state = load_state()
    last_status: Dict[str, int] = state.get("live_status_by_room", {})
    last_title:  Dict[str, str] = state.get("title_by_room", {})

    changed = False

    # 拉取当前状态
    try:
        current = fetch_rooms_info(rooms)
    except Exception as e:
        log(f"[ERROR] fetch rooms failed: {e}")
        return

    for rid in rooms:
        rid_str = str(rid)
        if rid_str not in current:
            log(f"[INFO]   no info for room {rid_str}")
            continue

        status, title = current[rid_str]
        prev = last_status.get(rid_str)

        if prev is None:
            # 第一次见到，只记录，不推送
            last_status[rid_str] = status
            last_title[rid_str]  = title
            log(f"[INFO]   initial state room {rid_str}: {status}")
            continue

        if status != prev:
            try:
                if status == 1:
                    send_telegram(format_live_on(rid_str, title))
                else:
                    # 下播时带上最后一次标题（如果当前标题为空）
                    send_telegram(format_live_off(rid_str, title or last_title.get(rid_str)))
                log(f"[OK]   room {rid_str} status change: {prev} -> {status}")
                last_status[rid_str] = status
                last_title[rid_str]  = title or last_title.get(rid_str, "")
                changed = True
            except Exception as e:
                log(f"[ERROR] telegram push failed for room {rid_str}: {e}")
        else:
            # 状态没变，更新一下标题缓存
            last_title[rid_str] = title or last_title.get(rid_str, "")
            log(f"[INFO]   room {rid_str} unchanged: {status}")

        time.sleep(0.2 + random.random() * 0.2)

    state["live_status_by_room"] = last_status
    state["title_by_room"]       = last_title
    save_state(state)

    if not changed:
        log("[INFO] No live status changes")

if __name__ == "__main__":
    main()
