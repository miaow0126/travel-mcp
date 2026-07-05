#!/usr/bin/env python3
"""桂晚的旅行手帐 —— 只读展示台

读取 travel-mcp 的存档目录，渲染成足迹时间轴 + 明信片墙风格的单页网站。
不依赖游戏进程，纯读文件，随时刷新随时看。

环境变量：
  DISPLAY_PORT   展示台端口（默认 8899）
  TRAVEL_DATA    存档目录（默认 /root/travel-data）
  TRAVEL_ASSETS  素材目录，用于纪念品本地图片（默认 /root/travel-mcp/assets）
"""

import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DISPLAY_PORT = int(os.environ.get("DISPLAY_PORT", 8899))
DATA_DIR = Path(os.environ.get("TRAVEL_DATA", "/root/travel-data"))
ASSETS_DIR = Path(os.environ.get("TRAVEL_ASSETS", "/root/travel-mcp/assets"))
STATIC_DIR = Path(os.environ.get("TRAVEL_STATIC", "/root/travel-mcp/data"))


# ── 读存档 ──────────────────────────────────────────────

def _j(name, default):
    p = DATA_DIR / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_trips():
    return _j("trips.json", [])


def load_wallet():
    return _j("wallet.json", {"balance": 0, "xp": 0, "ledger": []})


def load_diaries():
    return _j("diaries.json", [])


def load_souvenirs():
    return _j("souvenirs.json", [])


def load_state():
    return _j("state.json", {})


def load_visited_spots():
    return _j("visited_spots.json", [])


def resolve_dest_name(dest_id):
    """从 travel-mcp 自带的静态目的地库里查中文名（state.json 只存 id，不存名字）。"""
    p = STATIC_DIR / "destinations.json"
    try:
        dests = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return dest_id
    for d in dests:
        if d.get("id") == dest_id:
            return d.get("name_zh", dest_id)
    return dest_id


def trips_index():
    trips = load_trips()
    trips = sorted(trips, key=lambda t: t.get("at", ""), reverse=True)
    out = []
    for t in trips:
        out.append({
            "trip_id": t.get("trip_id"),
            "dest_name_zh": t.get("dest_name_zh", t.get("dest", "")),
            "days": t.get("days"),
            "party": t.get("party"),
            "style": t.get("style"),
            "spend": t.get("spend"),
            "xp": t.get("xp"),
            "first_visit": t.get("first_visit", False),
            "at": t.get("at"),
        })
    return out


def trip_detail(trip_id):
    trips = load_trips()
    trip = next((t for t in trips if t.get("trip_id") == trip_id), None)
    diaries = [d for d in load_diaries() if d.get("trip_id") == trip_id]
    souvenirs = [s for s in load_souvenirs() if s.get("trip_id") == trip_id]
    spots = [s for s in load_visited_spots() if s.get("trip_id") == trip_id]
    spots = sorted(spots, key=lambda s: s.get("at", ""))
    if not spots:
        # 老行程在加相册功能前就走完了，退而求其次：用 state.json 里仅剩的最后一站顶上
        st = load_state()
        if st.get("started_at") == trip_id:
            spot = (st.get("here_cache") or {}).get("p", {}).get("spot")
            if spot:
                spots = [{
                    "day": st.get("day"), "spot_id": spot.get("spot_id"),
                    "name_zh": spot.get("name_zh"), "name_en": spot.get("name_en"),
                    "blurb": spot.get("blurb"), "photo_url": spot.get("photo_url"),
                }]
    return {"trip": trip, "diaries": diaries, "souvenirs": souvenirs, "spots": spots}


def current_live_trip():
    """如果 state.json 里的行程还没走完，返回实时进度（附目的地中文名）；走完了返回 None。
    注意：这跟「已完成行程索引」（trips.json）是两回事——一趟新旅程开始后，在它结束、
    被写进 trips.json 之前，只能靠这个接口看到，足迹时间轴里还找不到它。"""
    st = load_state()
    if not st or st.get("phase") == "finished" or st.get("done"):
        return None
    st = dict(st)
    st["dest_name_zh"] = resolve_dest_name(st.get("dest", ""))
    return st


# ── 展示页面 ──────────────────────────────────────────────

PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>桂晚的旅行手帐</title>
<style>
:root {
  --bg: #0a0e14;
  --surface: #0f1420;
  --card: #141a28;
  --card-hover: #1a2136;
  --border: #202940;
  --text: #dde3f0;
  --muted: #5a6b8c;
  --dim: #34405c;
  --accent: #d4a24e;
  --accent-soft: #8a97b8;
  --sea: #3f7ba6;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'PingFang SC', 'Noto Sans SC', 'Helvetica Neue', sans-serif;
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.header {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--surface);
  flex-shrink: 0;
}
.header h1 { font-size: 1.15rem; font-weight: 600; }
.wallet {
  margin-left: auto;
  display: flex;
  gap: 18px;
  font-size: 0.8rem;
  color: var(--muted);
}
.wallet b { color: var(--accent); font-weight: 600; }
.main {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.left {
  width: 300px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  background: var(--surface);
}
.left-header {
  padding: 12px 18px;
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--surface);
}
.trip-item {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.12s;
  position: relative;
}
.trip-item:hover { background: var(--card-hover); }
.trip-item.active { background: var(--card); border-left: 2px solid var(--accent); }
.trip-item.live-entry { border-bottom-color: #4a3f1a; }
.trip-item.live-entry .trip-date { color: var(--accent); }
.trip-date { font-size: 0.68rem; color: var(--muted); margin-bottom: 4px; }
.trip-dest { font-size: 0.92rem; font-weight: 600; }
.trip-meta { font-size: 0.7rem; color: var(--accent-soft); margin-top: 4px; }
.no-trips {
  padding: 40px 20px;
  text-align: center;
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 2;
}
.right {
  flex: 1;
  overflow-y: auto;
  padding: 28px 32px;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--muted);
  font-size: 0.9rem;
  line-height: 2.5;
  text-align: center;
}
.empty-state .icon { font-size: 2rem; margin-bottom: 12px; opacity: 0.4; }

.live-banner {
  background: linear-gradient(90deg, #2a2410, #1a1608);
  border: 1px solid #4a3f1a;
  border-radius: 8px;
  padding: 10px 16px;
  margin-bottom: 20px;
  font-size: 0.78rem;
  color: var(--accent);
}

.story-header { margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
.story-title { font-size: 1.3rem; font-weight: 700; margin-bottom: 8px; }
.story-meta { font-size: 0.75rem; color: var(--muted); display: flex; gap: 16px; flex-wrap: wrap; }
.story-meta span.badge { color: var(--accent); }

.diary-text {
  font-size: 0.92rem;
  line-height: 2;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 28px;
}

.section-label {
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  margin: 24px 0 12px;
}

.souvenir-shelf {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.souvenir-card {
  width: 140px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.souvenir-card img {
  width: 100%;
  height: 100px;
  object-fit: cover;
  display: block;
  background: #1a2030;
}
.souvenir-info { padding: 8px 10px; }
.souvenir-name { font-size: 0.78rem; font-weight: 600; margin-bottom: 4px; }
.souvenir-line { font-size: 0.68rem; color: var(--muted); line-height: 1.5; }

.spot-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
  margin-bottom: 8px;
}
.spot-gallery-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.spot-gallery-card img {
  width: 100%;
  aspect-ratio: 16 / 9;
  height: auto;
  object-fit: cover;
  display: block;
  background: #1a2030;
}
.spot-gallery-body { padding: 10px 12px; }
.spot-gallery-name { font-size: 0.85rem; font-weight: 700; margin-bottom: 2px; }
.spot-gallery-name .en { color: var(--muted); font-weight: 400; font-size: 0.72rem; margin-left: 4px; }
.spot-gallery-blurb { font-size: 0.72rem; color: var(--accent-soft); line-height: 1.5; }
.spot-gallery-day { font-size: 0.62rem; color: var(--dim); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.08em; }

.live-spot-card {
  display: flex;
  gap: 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 16px;
}
.live-spot-card img { width: 200px; height: 140px; object-fit: cover; flex-shrink: 0; }
.live-spot-body { padding: 14px 16px 14px 0; flex: 1; }
.live-spot-name { font-size: 1rem; font-weight: 700; margin-bottom: 6px; }
.live-spot-blurb { font-size: 0.8rem; color: var(--accent-soft); margin-bottom: 8px; }
.live-spot-detail { font-size: 0.78rem; color: var(--muted); line-height: 1.7; }

@media (max-width: 700px) {
  .left { width: 100%; }
  .main { flex-direction: column; }
  .right { display: none; }
  .main.show-right .left { display: none; }
  .main.show-right .right { display: block; }
}
</style>
</head>
<body>
<div class="header">
  <span style="font-size:1.4rem">&#9992;&#65039;</span>
  <h1>桂晚的旅行手帐</h1>
  <div class="wallet" id="wallet">加载中…</div>
</div>
<div class="main" id="main">
  <div class="left">
    <div class="left-header">足迹时间轴</div>
    <div id="trip-list"><div class="no-trips">还没有旅行记录<br>去看看世界吧</div></div>
  </div>
  <div class="right" id="right-panel">
    <div class="empty-state">
      <div class="icon">&#127761;</div>
      <div>选择左侧一段旅程<br>看那段旅行的故事</div>
    </div>
  </div>
</div>
<script>
let trips = [];
let currentId = null;

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}

async function loadWallet() {
  const r = await fetch('/api/wallet');
  const w = await r.json();
  document.getElementById('wallet').innerHTML =
    `<span>&#127775; XP <b>${w.xp||0}</b></span><span>&#128176; 余额 <b>${w.balance||0}</b></span>`;
}

async function loadLive() {
  const r = await fetch('/api/current');
  const live = await r.json();
  return live;
}

async function loadTrips() {
  const [tripsData, live] = await Promise.all([
    fetch('/api/trips').then(r => r.json()),
    loadLive(),
  ]);
  trips = tripsData;
  const el = document.getElementById('trip-list');

  let html = '';
  if (live) {
    // 进行中的旅程还没走完，不在 trips.json 索引里，单独放一个入口，不跟已完成行程混在一起判断
    html += `
      <div class="trip-item live-entry" onclick="selectLive()" id="ti_live">
        <div class="trip-date">&#9203; 正在进行</div>
        <div class="trip-dest">${esc(live.dest_name_zh || live.dest || '')}</div>
        <div class="trip-meta">第${live.day||'?'}天</div>
      </div>`;
  }
  if (!trips.length && !live) {
    html = '<div class="no-trips">还没有旅行记录<br>去看看世界吧</div>';
  } else {
    html += trips.map(t => `
      <div class="trip-item" onclick="selectTrip('${esc(t.trip_id)}')" id="ti_${esc(t.trip_id)}">
        <div class="trip-date">${fmtDate(t.at)}</div>
        <div class="trip-dest">${esc(t.dest_name_zh)} ${t.first_visit ? '&#10024;' : ''}</div>
        <div class="trip-meta">${t.days}天 · ${esc(t.style||'')} · +${t.xp||0}XP</div>
      </div>
    `).join('');
  }
  el.innerHTML = html;

  if (!currentId) {
    if (live) selectLive();
    else if (trips.length) selectTrip(trips[0].trip_id);
  }
}

async function selectLive() {
  currentId = '__live__';
  document.querySelectorAll('.trip-item').forEach(el => el.classList.remove('active'));
  const el = document.getElementById('ti_live');
  if (el) el.classList.add('active');

  const live = await loadLive();
  const panel = document.getElementById('right-panel');
  if (!live) {
    panel.innerHTML = `<div class="empty-state"><div class="icon">&#127761;</div><div>这段旅程刚刚收尾<br>刷新一下时间轴看看</div></div>`;
    return;
  }
  const spot = live.here_cache && live.here_cache.p && live.here_cache.p.spot;
  let html = `<div class="live-banner">&#9203; 这段旅程还在进行中 · 第${live.day||'?'}天</div>`;
  html += `
    <div class="story-header">
      <div class="story-title">${esc(live.dest_name_zh || '')}</div>
      <div class="story-meta">
        <span>${esc(live.party||'')}</span>
        <span>${esc(live.style||'')}</span>
      </div>
    </div>
  `;
  if (spot) {
    html += `
      <div class="live-spot-card">
        ${spot.photo_url ? `<img src="${esc(spot.photo_url)}" loading="lazy">` : ''}
        <div class="live-spot-body">
          <div class="live-spot-name">${esc(spot.name_zh)} <span style="color:var(--muted);font-weight:400">${esc(spot.name_en||'')}</span></div>
          <div class="live-spot-blurb">${esc(spot.blurb||'')}</div>
          <div class="live-spot-detail">${(spot.details||[]).map(esc).join('<br>')}</div>
        </div>
      </div>`;
  }
  panel.innerHTML = html;
}

async function selectTrip(tripId) {
  currentId = tripId;
  document.querySelectorAll('.trip-item').forEach(el => el.classList.remove('active'));
  const el = document.getElementById('ti_' + tripId);
  if (el) el.classList.add('active');

  const r = await fetch('/api/trip?id=' + encodeURIComponent(tripId));
  const d = await r.json();
  const panel = document.getElementById('right-panel');
  const trip = d.trip || {};
  const diary = (d.diaries && d.diaries[0]) || null;
  const souvenirs = d.souvenirs || [];
  const spots = d.spots || [];

  let html = `
    <div class="story-header">
      <div class="story-title">${esc((diary && diary.title) || trip.dest_name_zh || '')}</div>
      <div class="story-meta">
        <span>${fmtDate(trip.at)}</span>
        <span class="badge">${trip.days||'?'}天</span>
        <span>${esc(trip.party||'')}</span>
        <span>${esc(trip.style||'')}</span>
        <span>花费 ${trip.spend||0}</span>
        ${trip.first_visit ? '<span class="badge">&#10024; 首访</span>' : ''}
      </div>
    </div>
  `;

  const photoSpots = spots.filter(s => s.photo_url);
  if (photoSpots.length) {
    html += `<div class="section-label">&#128247; 沿途风光</div><div class="spot-gallery">`;
    for (const s of photoSpots) {
      html += `
        <div class="spot-gallery-card">
          <img src="${esc(s.photo_url)}" loading="lazy">
          <div class="spot-gallery-body">
            <div class="spot-gallery-day">第${s.day||'?'}天</div>
            <div class="spot-gallery-name">${esc(s.name_zh)}<span class="en">${esc(s.name_en||'')}</span></div>
            <div class="spot-gallery-blurb">${esc(s.blurb||'')}</div>
          </div>
        </div>`;
    }
    html += `</div>`;
  }

  if (diary) {
    html += `<div class="diary-text">${esc(diary.text)}</div>`;
  } else {
    html += `<div class="diary-text" style="color:var(--muted)">这段旅程还没有写下日记。</div>`;
  }

  if (souvenirs.length) {
    html += `<div class="section-label">&#127873; 纪念品架</div><div class="souvenir-shelf">`;
    for (const s of souvenirs) {
      const imgSrc = s.image ? ('/assets/' + s.image.replace(/^assets\//, '')) : '';
      html += `
        <div class="souvenir-card">
          ${imgSrc ? `<img src="${esc(imgSrc)}" loading="lazy" onerror="this.style.display='none'">` : ''}
          <div class="souvenir-info">
            <div class="souvenir-name">${esc(s.name)}</div>
            <div class="souvenir-line">${esc(s.line||'')}</div>
          </div>
        </div>`;
    }
    html += `</div>`;
  }

  panel.innerHTML = html;
}

loadWallet();
loadTrips();
setInterval(async () => {
  await loadWallet();
  await loadTrips();
  if (currentId === '__live__') await selectLive();
  else if (currentId) await selectTrip(currentId);
}, 30000);
</script>
</body>
</html>"""


class DisplayHandler(BaseHTTPRequestHandler):
    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/trips":
            self._json(trips_index())
        elif path == "/api/wallet":
            self._json(load_wallet())
        elif path == "/api/current":
            self._json(current_live_trip())
        elif path == "/api/trip":
            qs = parse_qs(parsed.query)
            trip_id = qs.get("id", [""])[0]
            self._json(trip_detail(trip_id))
        elif path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            fp = (ASSETS_DIR / rel).resolve()
            try:
                fp.relative_to(ASSETS_DIR.resolve())
            except ValueError:
                self.send_response(403)
                self.end_headers()
                return
            if not fp.exists() or not fp.is_file():
                self.send_response(404)
                self.end_headers()
                return
            ctype = "image/jpeg"
            if fp.suffix.lower() == ".png":
                ctype = "image/png"
            elif fp.suffix.lower() in (".jpg", ".jpeg"):
                ctype = "image/jpeg"
            elif fp.suffix.lower() == ".webp":
                ctype = "image/webp"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(fp.read_bytes())
        else:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"[*] 旅行手帐展示台启动")
    print(f"    端口: {DISPLAY_PORT}")
    print(f"    存档目录: {DATA_DIR}")
    print(f"    素材目录: {ASSETS_DIR}")
    server = ThreadingHTTPServer(("0.0.0.0", DISPLAY_PORT), DisplayHandler)
    server.serve_forever()
