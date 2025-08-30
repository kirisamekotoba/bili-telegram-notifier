import os, json, time, random
from pathlib import Path
from typing import Dict, Optional
import requests

# ====== 配置 / 环境 ======
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]
# 依旧使用 BILI_UIDS（用逗号分隔），表示要监控的 UP 主 UID
BILI_UIDS    = [u.strip() for u in os.environ["BILI_UIDS"].split(",") if u.strip()]

STATE_FILE = Path("state.json")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Actions; BiliLive/1.0)",
    "Accept": "application/json, */*;q=0.1",
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
})

# ====== 工具 ======
def log(msg: str): print(msg, flush=True)

def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text("utf-8"))
            if "live_status" in data:
                return data
        except Exception as e:
            log(f"[WARN] load_state failed: {e}")
    # 结构：{"live_status": {uid: 0/1}, "room": {uid: room_id}}
    return {"live_status": {}, "room": {}}

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

def format_live_on(uid: str, title: str, room_id: int):
    url = f"https://live.bilibili.com/{room_id}"
    head = f"🟢 <b>UP {uid} 开播啦</b>"
    body = f"🎯 {title}" if title else "🎯 直播开始"
    return f"{head}\n{body}\n🔗 {url}"

def format_live_off(uid: str, room_id: int, title: Optional[str] = None):
    url = f"https://live.bilibili.com/{room_id}"
    head = f"⚪ <b>UP {uid} 已下播</b>"
    tail = f"\n🔗 {url}"
    if title:
        return f"{head}\n📝 {title}{tail}"
    return f"{head}{tail}"

# ====== 数据源（尽量稳健）======
# 1) 首选：空间信息接口，含 live_room 区块（无需登录）
ACC_INFO = "https://api.bilibili.com/x/space/acc/info?mid={uid}&jsonp=jsonp"

# 2) 兜底：旧接口，通过 uid 拿房间 id 与状态
ROOM_INFO_OLD = "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld?mid={uid}"

def get_live_info(uid: str):
    """
    返回 (status, room_id, title)
    status: 0 未开播 / 1 开播
    若拿不到，返回 (None, None, None)
    """
    # 首先尝试 acc_info
    try:
        r = SESSION.get(ACC_INFO.format(uid=uid), timeout=15)
        r.raise_for_status()
        j = r.json()
        live = (j.get("data") or {}).get("live_room") or {}
        room_id = live.get("roomid") or live.get("room_id")
        status  = live.get("liveStatus") or live.get("live_status") or live.get("status")
        title   = live.get("title")
        # 部分字段命名差异处理
        if status in (0, 1) and room_id:
            return int(status), int(room_id), (title or "")
    except Exception as e:
        log(f"[WARN] acc_info failed for {uid}: {e}")

    # 再尝试旧接口
    try:
        r = SESSION.get(ROOM_INFO_OLD.format(uid=uid), timeout=15)
        r.raise_for_status()
        j = r.json()
        data = j.get("data") or {}
        room_id = data.get("roomid") or data.get("room_id")
        status  = data.get("liveStatus") or data.get("live_status")
        title   = data.get("title") or ""
        if status in (0, 1) and room_id:
            return int(status), int(room_id), title
    except Exception as e:
        log(f"[WARN] room_info_old failed for {uid}: {e}")

    return None, None, None

# ====== 主流程：只在状态变化时推送 ======
def main():
    state = load_state()
    last = state.get("live_status", {})
    rooms = state.get("room", {})

    changed = False

    for uid in BILI_UIDS:
        log(f"[INFO] Checking live status for UID {uid} …")
        status, room_id, title = get_live_info(uid)
        if status is None:
            log(f"[INFO]   no live info for {uid}")
            continue

        prev = last.get(uid)
        rooms[uid] = room_id  # 记录房间号以便下播时也能给链接

        if prev is None:
            # 第一次见到，记录状态但不推送，避免历史状态误报
            last[uid] = status
            log(f"[INFO]   initial state for {uid}: {status}")
            continue

        if status != prev:
            # 发生变化：0->1 开播，1->0 下播
            try:
                if status == 1:
                    send_telegram(format_live_on(uid, title, room_id))
                else:
                    send_telegram(format_live_off(uid, room_id, title))
                log(f"[OK]   pushed status change for {uid}: {prev} -> {status}")
                last[uid] = status
                changed = True
            except Exception as e:
                log(f"[ERROR] telegram push failed for {uid}: {e}")
        else:
            log(f"[INFO]   unchanged for {uid}: {status}")

        # 小小节流，避免接口触发风控
        time.sleep(0.3 + random.random() * 0.3)

    state["live_status"] = last
    state["room"] = rooms
    save_state(state)

    if not changed:
        log("[INFO] No live status changes")

if __name__ == "__main__":
    main()
