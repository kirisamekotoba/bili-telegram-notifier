import os, json, time, requests
from pathlib import Path

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID   = os.environ["TG_CHAT_ID"]
BILI_UIDS    = [u.strip() for u in os.environ["BILI_UIDS"].split(",") if u.strip()]

STATE_FILE = Path("state.json")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (GitHub Actions; BiliWatch/1.0)"})

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except:
            pass
    return {"seen": {}}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

def fetch(uid):
    url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    items = r.json().get("data", {}).get("items", [])
    results = []
    for it in items:
        id_str = str(it.get("id_str") or it.get("id"))
        title = "B站新动态"
        link = f"https://t.bilibili.com/{id_str}"
        results.append((id_str, title, link))
    return results

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    r = session.post(url, json={
        "chat_id": TG_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }, timeout=20)
    r.raise_for_status()

def main():
    state = load_state()
    seen = state.get("seen", {})

    for uid in BILI_UIDS:
        items = fetch(uid)
        already = set(seen.get(uid, []))
        new_items = [it for it in items if it[0] not in already]

        for id_str, title, link in new_items[:3][::-1]:
            msg = f"👀 UP {uid} 有新动态\n{title}\n🔗 {link}"
            send_telegram(msg)
            already.add(id_str)
            time.sleep(0.5)

        seen[uid] = list(already)[-100:]

    state["seen"] = seen
    save_state(state)

if __name__ == "__main__":
    main()
