#!/usr/bin/env python3
"""Kaleidoscope Online - live radio station for blind people, South Africa and overseas.
Endless MP3 stream + accessible landing page + DJ features:
live microphone injection, skip, shuffle, play-next queue. Port 8000."""
import os, random, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RADIO = os.environ.get("RADIO_DIR", "/home/user/radio")
MUSIC = RADIO + "/music"
LIVE = RADIO + "/live"
INCOMING = RADIO + "/incoming"
NP = RADIO + "/nowplaying.txt"
MODE = RADIO + "/mode.json"      # {"shuffle": 1}
SKIPF = RADIO + "/skip.flag"
NEXTQ = RADIO + "/next.txt"
SCHED = RADIO + "/sched.json"
BREAKF = RADIO + "/break.json"
STORYF = RADIO + "/story.json"
PROGF = RADIO + "/programs.json"
BREAKD = RADIO + "/.break_done"      # one filename per line, played next
STATION = {"name": "Kaleidoscope Online", "tagline": "Radio for blind people, in South Africa and overseas"}
for d in (MUSIC, LIVE, INCOMING, BREAKD):
    os.makedirs(d, exist_ok=True)

lock = threading.Lock()
now_playing = {"song": "Kaleidoscope Online - starting", "listeners": 0}

# ---------------- shared broadcast (one encoder, many listeners) ----------------
import queue as _PQUEUE

class _Client:
    __slots__ = ("q", "dead")
    def __init__(self):
        self.q = _PQUEUE.Queue(maxsize=4)
        self.dead = False

CLIENTS = set()
CLIENTS_LOCK = threading.Lock()

def client_count():
    with CLIENTS_LOCK:
        return sum(1 for c in CLIENTS if not c.dead)

def broadcast(data):
    """Push one chunk to every listener. Dead/slow clients are dropped, never block others."""
    dead = []
    with CLIENTS_LOCK:
        for c in CLIENTS:
            if c.dead:
                dead.append(c); continue
            try:
                c.q.put_nowait(data)
            except _PQUEUE.Full:
                c.dead = True; dead.append(c)
        for c in dead:
            CLIENTS.discard(c)

def paced_broadcast(data, br, burst):
    broadcast(data)
    if burst[0] > 0:
        burst[0] -= len(data)
    else:
        time.sleep(len(data) * 8.0 / br * 0.94)


def pretty(s):
    return (s[:-4].replace("gsp_EN_", "").replace("gsp_AF_", "")
            .replace("prog_", "").replace("stryd_", "").replace("id_", "")
            .replace("gsp_", "").replace("_", " ").strip())

def save_np():
    try:
        with open(NP, "w") as f:
            f.write(now_playing["song"])
    except Exception:
        pass

_br_cache = {}
def bitrate_of(path):
    try:
        if path in _br_cache and _br_cache.get(path + "_size") == os.path.getsize(path):
            return _br_cache[path]
        with open(path, "rb") as f:
            data = f.read(65536)
        start = 0
        if data[:3] == b"ID3":
            try:
                size = ((data[6] & 0x7f) << 21) | ((data[7] & 0x7f) << 14) | ((data[8] & 0x7f) << 7) | (data[9] & 0x7f)
                start = 10 + size
            except Exception:
                start = 0
        # raw WAV from a phone microphone: work out the bitrate from the fmt chunk
        if data[:4] == b"RIFF":
            try:
                ch = int.from_bytes(data[22:24], "little") or 1
                rate = int.from_bytes(data[24:28], "little") or 16000
                bits = int.from_bytes(data[34:36], "little") or 16
                return ch * rate * bits
            except Exception:
                return 256000
        i = data.find(b"\xff\xfb", start)
        if i < 0:
            i = data.find(b"\xff\xfa", start)
        if i < 0 or i + 4 > len(data):
            return 128000
        table = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
        br = table[(data[i + 2] >> 4) & 0x0F] * 1000
        if br == 0:
            return 128000
        _br_cache[path] = br
        _br_cache[path + "_size"] = os.path.getsize(path)
        return br
    except Exception:
        return 128000

MANIFEST_URL = os.environ.get("MANIFEST_URL",
    "https://github.com/avanderberg48-collab/mandy-radio-library/releases/download/library/station_music.json")
MANI = {"map": {}, "loaded": 0}
import urllib.request as _U


def manifest(force=False):
    """name -> public mp3 url, refreshed every 15 minutes (no token needed)."""
    if not force and MANI["map"] and time.time() - MANI["loaded"] < 900:
        return MANI["map"]
    try:
        d = json.load(_U.urlopen(MANIFEST_URL, timeout=60))
        mp = {}
        for s in d.get("songs", []):
            n = s.get("name")
            u = s.get("url")
            if n and u:
                mp[n] = u
        if mp:
            MANI["map"] = mp
            MANI["loaded"] = time.time()
    except Exception as e:
        blog("manifest error %s" % str(e)[:80])
    return MANI["map"]


class RemoteFile:
    """A song opened straight from the public GitHub link, with seek for mic holds."""

    def __init__(self, url):
        self.url = url
        self.pos = 0
        self.r = _U.urlopen(url, timeout=90)

    def read(self, n):
        d = self.r.read(n)
        self.pos += len(d or b"")
        return d

    def tell(self):
        return self.pos

    def seek(self, p):
        try:
            self.r.close()
        except Exception:
            pass
        self.r = _U.urlopen(self.url, timeout=90)
        left = p
        while left > 0:
            chunk = self.r.read(min(262144, left))
            if not chunk:
                break
            left -= len(chunk)
        self.pos = p - left

    def close(self):
        try:
            self.r.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def open_media(name):
    p = os.path.join(MUSIC, name)
    if os.path.exists(p) and os.path.getsize(p) > 1000:
        return open(p, "rb")
    u = manifest().get(name)
    if not u:
        raise IOError("not found: " + name)
    return RemoteFile(u)


def library():
    try:
        local = [f for f in os.listdir(MUSIC) if f.lower().endswith(".mp3") and not f.endswith(".mp3.part")]
        names = set(local) | set(manifest().keys())
        return sorted(x for x in names if not x.startswith("silence"))
    except Exception:
        return []

def live_files():
    try:
        return [os.path.join(LIVE, f) for f in sorted(os.listdir(LIVE))
                if f.endswith(".mp3") or f.endswith(".wav")]
    except Exception:
        return []

PAUSEDF = RADIO + "/.paused"
LASTLIVE = RADIO + "/.lastlive"
SILENCE = MUSIC + "/silence.mp3"

def mic_recent(window=8):
    """True while the DJ's microphone has been sending audio in the last few seconds."""
    try:
        return (time.time() - os.path.getmtime(LASTLIVE)) < window
    except Exception:
        return False


def music_paused():
    try:
        return os.path.exists(PAUSEDF)
    except Exception:
        return False

def make_silence():
    if not os.path.exists(SILENCE) or os.path.getsize(SILENCE) < 2000:
        try:
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                           "-i", "anullsrc=r=44100:cl=stereo", "-t", "10",
                           "-b:a", "128k", SILENCE], capture_output=True)
        except Exception:
            pass

def take_skip():
    try:
        if os.path.exists(SKIPF):
            os.remove(SKIPF)
            return True
    except Exception:
        pass
    return False

def radio_mode():
    try:
        with open(MODE) as f:
            return json.load(f)
    except Exception:
        return {"shuffle": 1}

def sched_active():
    """Scheduled album blocks from sched.json.
    Legacy single block: {"prefix","start","until"} (one-time, epoch based).
    Weekly blocks: {"blocks":[{"prefix","days":[0-6 Mon-Sun],"start_h","end_h"}]} in SA local time.
    Returns the active prefix or None."""
    try:
        s = json.load(open(SCHED))
        if s.get("prefix") and float(s.get("start", 0)) <= time.time() < float(s.get("until", 0)):
            return str(s["prefix"])
        t = time.localtime(time.time() + 7200)  # SA local time (UTC+2)
        for b in s.get("blocks", []):
            days = b.get("days")
            if days is not None and t.tm_wday not in days:
                continue
            if int(b.get("start_h", 0)) <= t.tm_hour < int(b.get("end_h", 24)):
                p = str(b.get("prefix", "")).strip()
                if p:
                    return p
    except Exception:
        pass
    return None

BREAKLOG = RADIO + "/break.log"


def blog(msg):
    """Append a line to the break log (kept small: last 400 lines)."""
    try:
        with open(BREAKLOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        try:
            lines = open(BREAKLOG).read().splitlines()
            if len(lines) > 400:
                with open(BREAKLOG, "w") as f:
                    f.write("\n".join(lines[-300:]) + "\n")
        except Exception:
            pass
    except Exception:
        pass


def take_break():
    """Scheduled one-time breaks (story/programs): {"breaks":[{"at","files"},...]}.
    Each break plays once per machine within a 30-min window after its time."""
    try:
        b = json.load(open(BREAKF))
        breaks = b.get("breaks") if isinstance(b, dict) else None
        if not breaks and b.get("at"):
            breaks = [b]  # old single-break format
        now = time.time()
        lib = set(library())
        for i, br in enumerate(breaks or []):
            at = float(br.get("at", 0))
            files = br.get("files") or []
            mk = "%s_%d" % (BREAKD, i)
            if files and at <= now < at + 1800 and not os.path.exists(mk):
                open(mk, "w").write(str(now))
                return [f for f in files if f in lib]

    except Exception:
        pass
    # daily weekday story
    try:
        st = story_due()
        if st:
            mk, fname = st
            lib = set(library())
            if fname in lib:
                open(mk, 'w').write(str(time.time()))
                blog("STORY_PLAY " + fname)
                return [fname]
            else:
                blog("STORY_WAIT file not in library yet: " + fname + " (library=" + str(len(lib)) + ")")
    except Exception as e:
        blog("STORY_ERROR " + str(e)[:120])
    # recurring daily programs
    try:
        pr = programs_due() or []
        if pr:
            blog("PROGRAM_PLAY " + ",".join(pr))
        return pr
    except Exception as e:
        blog("PROGRAM_ERROR " + str(e)[:120])
    return []

def story_due():
    """Daily weekday story: episode derived from the calendar (survives machine swaps).
    story.json: {"start","base","hour","prefix"} - plays between :00 and :25 of the hour."""
    try:
        import datetime
        s = json.load(open(STORYF))
        t = time.localtime(time.time() + 7200)  # station hours are South Africa (UTC+2)
        hour = int(s.get("hour", 11))
        if t.tm_wday > 4 or t.tm_hour != hour or t.tm_min >= 45:
            return None
        start = datetime.date.fromisoformat(str(s.get("start", "2026-10-27")))
        today = datetime.date(t.tm_year, t.tm_mon, t.tm_mday)
        if today < start:
            return None
        ep = int(s.get("base", 2))
        one = datetime.timedelta(days=1)
        d = start
        while d < today:
            d += one
            if d.weekday() < 5:
                ep += 1
        fname = "%s%d.mp3" % (str(s.get("prefix", "stryd_Die_Bankroof_Afl_")), ep)
        mk = "%s_story_%s_%d" % (BREAKD, str(today), ep)
        if os.path.exists(mk):
            return None
        return mk, fname
    except Exception:
        return None


def programs_due():
    """Recurring daily programs (programs.json: {"programs":[{"hour","file"}]}).
    Play between :00 and :25 of the hour, once per day per program."""
    try:
        p = json.load(open(PROGF))
        t = time.localtime(time.time() + 7200)  # station hours are South Africa (UTC+2)
        today = "%d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
        lib = set(library())
        out = []
        for pr in p.get("programs", []):
            if t.tm_hour != int(pr.get("hour", 0)) or t.tm_min >= 25:
                continue
            f = str(pr.get("file", ""))
            mk = "%s_prog_%s_%s" % (BREAKD, f[:24], today)
            if not f or f not in lib or os.path.exists(mk):
                continue
            open(mk, "w").write(str(time.time()))
            out.append(f)
        return out
    except Exception:
        return []

def filter_program(songs):
    """During a scheduled program (e.g. gospel), play only matching songs."""
    m = radio_mode()
    only = str(m.get("only", "") or "")
    until = float(m.get("until", 0) or 0)
    if only and time.time() < until:
        return [s for s in songs if s.startswith(only)], only, until
    return songs, "", 0

def take_next_queue():
    try:
        with open(NEXTQ) as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception:
        return []
    try:
        open(NEXTQ, "w").close()
    except Exception:
        pass
    lib = set(library())
    return [l for l in lines if l in lib]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def page(self):
        return """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kaleidoscope Online</title>
<style>
body{font-family:Verdana,sans-serif;background:#101828;color:#fff;text-align:center;padding:40px 16px}
h1{font-size:44px;color:#7ee0ff} .tag{font-size:22px;color:#ffd166;margin-bottom:30px}
button{font-size:30px;padding:26px 60px;border-radius:16px;border:none;background:#7ee0ff;color:#0a1120;font-weight:bold;cursor:pointer}
.np{font-size:20px;margin-top:24px;color:#cfe9ff}
</style></head><body>
<h1>Kaleidoscope Online</h1>
<p class="tag">Radio for blind people, in South Africa and overseas</p>
<button onclick="play()" aria-label="Play the radio">Play</button>
<p class="np" id="np">Loading...</p>
<audio id="au" autoplay></audio>
<script>
function play(){var a=document.getElementById('au');a.src='/stream';a.play().catch(()=>{});}
setInterval(function(){fetch('/np').then(r=>r.json()).then(d=>{document.getElementById('np').textContent='Now playing: '+d.song;}).catch(()=>{});},10000);
play();
</script></body></html>"""

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    # ---------- remote control for cloud hosts (no shell access) ----------
    def do_POST(self):
        import base64 as _b64
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if u.path == "/upload":
            q = parse_qs(u.query)
            name = (q.get("name", ["upload.mp3"])[0] or "upload.mp3")
            name = "".join(c for c in name if c.isalnum() or c in "._- ")[:90]
            dest = os.path.join(INCOMING, name)
            with open(dest, "wb") as f:
                f.write(body)
            return self._json({"ok": 1, "saved": name, "size": len(body)})
        if u.path != "/ctl":
            self.send_response(404); self.end_headers(); return
        try:
            j = json.loads(body.decode("utf-8", "replace") or "{}")
        except Exception:
            j = {}
        cmd = str(j.get("cmd", ""))
        try:
            if cmd == "pause":
                open(PAUSEDF, "w").close()
            elif cmd == "resume":
                for f in (PAUSEDF, RADIO + "/.lastlive"):
                    try: os.remove(f)
                    except Exception: pass
            elif cmd == "skip":
                open(SKIPF, "w").close()
            elif cmd == "shuffle":
                with open(MODE, "w") as f:
                    json.dump({"shuffle": 1 if j.get("on") else 0}, f)
            elif cmd == "program":
                hours = max(0.25, min(12.0, float(j.get("hours") or 4)))
                until = int(time.time() + hours * 3600)
                with open(MODE, "w") as f:
                    json.dump({"shuffle": 1, "only": "gsp_", "until": until}, f)
            elif cmd == "program_off":
                with open(MODE, "w") as f:
                    json.dump({"shuffle": 1}, f)
            elif cmd == "playnext":
                nm = "".join(c for c in str(j.get("name", "")) if c.isalnum() or c in " _.-").replace(" ", "_")
                if nm and os.path.exists(os.path.join(MUSIC, nm)):
                    with open(NEXTQ, "a") as f:
                        f.write(nm + "\n")
                    return self._json({"result": "QUEUED"})
                return self._json({"result": "MISSING"})
            elif cmd == "write":
                path = str(j.get("path", ""))
                if path.startswith(RADIO) and ".." not in path:
                    with open(path, "w") as f:
                        f.write(str(j.get("content", "")))
            elif cmd == "upchunk":
                cid = "".join(c for c in str(j.get("id", "")) if c.isalnum() or c in "_-")[:40]
                seq = "".join(c for c in str(j.get("seq", "0")) if c.isdigit())[:6].zfill(6)
                data = _b64.b64decode(str(j.get("b64", "")))
                with open(os.path.join(INCOMING, "p_%s_%s.part" % (cid, seq)), "wb") as f:
                    f.write(data)
                return self._json({"ok": 1, "part": seq, "size": len(data)})
            elif cmd == "upend":
                return self._json(do_upend(j))
            else:
                return self._json({"ok": 0, "error": "unknown cmd"})
        except Exception as e:
            return self._json({"ok": 0, "error": str(e)[:200]})
        return self._json({"ok": 1, "cmd": cmd})

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            b = self.page().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/np":
            with lock:
                obj = {"song": now_playing["song"], "listeners": now_playing["listeners"],
                       "paused": music_paused(), "live": bool(live_files()),
                       "engine": "2026-10-31d"}
            self._json(obj)
        elif self.path == "/library":
            self._json({"station": STATION, "songs": library()})
        elif self.path == "/dj":
            m = radio_mode()
            only, until = str(m.get("only", "") or ""), float(m.get("until", 0) or 0)
            active = 1 if (only and time.time() < until) else 0
            self._json({"shuffle": 1 if m.get("shuffle", 1) else 0,
                        "program": only, "program_active": active,
                        "program_until": int(until),
                        "songs": library(),
                        "live_waiting": len(live_files()),
                        "listeners": now_playing["listeners"]})
        elif self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            c = _Client()
            with CLIENTS_LOCK:
                CLIENTS.add(c)
            with lock:
                now_playing["listeners"] = client_count()
            try:
                self.wfile.flush()
                while not c.dead:
                    try:
                        d = c.q.get(timeout=30)
                    except Exception:
                        continue
                    self.wfile.write(d)
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                c.dead = True
                with CLIENTS_LOCK:
                    CLIENTS.discard(c)
                with lock:
                    now_playing["listeners"] = client_count()
        else:
            self.send_response(404)
            self.end_headers()

def _send_live(burst):
    """Play any waiting live mic chunks with absolute priority (station wide)."""
    while True:
        lf = live_files()
        if not lf:
            return
        try:
            open(LASTLIVE, "w").close()
        except Exception:
            pass
        with lock:
            now_playing["song"] = "Live broadcast - microphone"
            save_np()
        for lv in lf:
            br = bitrate_of(lv)
            amount = max(2048, int(br / 4))
            with open(lv, "rb") as f:
                while True:
                    d = f.read(amount)
                    if not d:
                        break
                    paced_broadcast(d, br, burst)
            try:
                os.remove(lv)
            except Exception:
                pass


def do_upend(j):
    """Finish an upload: join the parts, convert to mp3, drop it where it belongs."""
    import glob, shutil, subprocess
    asong = str(j.get("kind") == "mic")
    cid = "".join(c for c in str(j.get("id", "")) if c.isalnum() or c in "_-")[:40]
    if not cid:
        return {"error": "no id"}
    raw = "".join(c for c in str(j.get("name", "upload.mp3")) if c.isalnum() or c in "._- ")
    ext = (raw.lower().rsplit(".", 1)[-1] if "." in raw else "mp3")
    nm = raw.rsplit(".", 1)[0][:60].replace(" ", "_") or "upload"
    if j.get("kind") != "mic" and "jingle" in nm.lower() and not nm.startswith("id"):
        nm = "id_" + nm
    parts = sorted(glob.glob(os.path.join(INCOMING, "p_%s_*.part" % cid)))
    if not parts:
        return {"error": "no parts"}
    stamp = int(time.time() * 1000)
    tmp = os.path.join(INCOMING, "t_%s.dat" % cid)
    with open(tmp, "wb") as out:
        for pp in parts:
            with open(pp, "rb") as f:
                shutil.copyfileobj(f, out)
    if j.get("kind") == "mic":
        target = os.path.join(LIVE, "live_%d.mp3" % stamp)
    else:
        target = os.path.join(MUSIC, nm + ".mp3")
    ok = False
    if ext == "mp3":
        os.replace(tmp, target); ok = True
    else:
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-vn", "-map_metadata", "-1",
                            "-ar", "44100", "-ac", "2", "-b:a", "128k", target], check=True, timeout=120)
            os.remove(tmp); ok = True
        except Exception:
            try: os.replace(tmp, target); ok = True
            except Exception: pass
    for pp in parts:
        try: os.remove(pp)
        except Exception: pass
    if j.get("kind") == "mic":
        lives = sorted(glob.glob(os.path.join(LIVE, "*.mp3")), key=os.path.getmtime)
        for old in lives[:-20]:
            try: os.remove(old)
            except Exception: pass
    return {"ok": 1 if ok else 0, "saved": os.path.basename(target), "result": "UPLOAD_OK" if ok else "UPLOAD_FAIL"}


def producer_loop():
    """THE station: one thread reads the music and broadcasts to every listener."""
    burst = [32000]  # ~2s cushion: listeners (and the DJ app monitor) hear changes sooner
    last_ad = [""]
    jingle_i = [0]
    while True:
        # DJ hold mode: music paused, mic (if live) plays, silence otherwise
        if music_paused() or mic_recent():
            if live_files():
                _send_live(burst)
            else:
                make_silence()
                with lock:
                    now_playing["song"] = "Station paused - DJ hold"
                    save_np()
                # stream the hold-silence in small slices so a live microphone is picked up
                # within a fraction of a second instead of waiting for a whole file
                try:
                    br = bitrate_of(SILENCE)
                    slicebytes = max(2048, int(br / 16))  # about a quarter of a second
                    total = os.path.getsize(SILENCE)
                    pos = 0
                    while music_paused() or mic_recent():
                        if live_files():
                            _send_live(burst)
                            pos = 0
                            continue
                        if pos >= total:
                            pos = 0
                        with open(SILENCE, "rb") as f:
                            f.seek(pos)
                            d = f.read(slicebytes)
                        if not d:
                            pos = 0
                            continue
                        pos += len(d)
                        paced_broadcast(d, br, burst)
                except Exception:
                    pass
            continue
        songs = library()
        songs, prog_only, prog_until = filter_program(songs)
        schedp = sched_active()
        if schedp:
            block = [s for s in songs if s.startswith(schedp)]
            if block:
                songs = block
                prog_only = schedp
            else:
                # nothing filed under this program prefix: never leave the station silent
                blog("SCHED_EMPTY " + schedp)
                schedp = None
        if not songs:
            if live_files():
                _send_live(burst)
            time.sleep(0.5)
            continue
        ads = [s for s in songs if s.startswith("ad_") and "_station" not in s]
        # the talking station jingles, Afrikaans then English, played after every second song
        ids = sorted([s for s in songs if "_station" in s and s.startswith("ad_")])
        ids = ids or [s for s in songs if s.startswith("id")]
        queue = [s for s in songs if not s.startswith("id")
                 and not s.startswith("ad_")
                 and "jingo" not in s.lower() and "jingle" not in s.lower()
                 and (schedp is not None or not s.startswith("prog"))
                 and not s.startswith("stryd")]
        nxt = take_next_queue()   # what the DJ queues always gets played, block or not
        if queue:
            if schedp:
                queue.sort()  # album block: original track order
            elif radio_mode().get("shuffle", 1):
                random.shuffle(queue)
            else:
                queue.sort()
        if not queue:
            time.sleep(5)
            continue
        def play_one(name):
            """Play one file start-to-finish for everyone (used by scheduled breaks)."""
            p = os.path.join(MUSIC, name)
            try:
                with lock:
                    now_playing["song"] = pretty(name)
                    save_np()
                br = bitrate_of(p)
                amount = max(2048, int(br / 4))
                with open_media(name) as f:
                    while True:
                        d = f.read(amount)
                        if not d:
                            break
                        paced_broadcast(d, br, burst)
            except Exception:
                pass

        cur_sched = schedp
        ordered = take_break() + list(nxt)
        for i, s in enumerate(queue):
            ordered.append(s)
            n = i + 1
            if n % 8 == 0 and ads:
                # every 8th gap: a radio advert (rotating, never the same twice in a row)
                pick = random.choice(ads)
                if len(ads) > 1 and pick == last_ad[0]:
                    others = [a for a in ads if a != last_ad[0]]
                    pick = random.choice(others if others else ads)
                last_ad[0] = pick
                ordered.append(pick)
            elif n % 2 == 0 and ids:
                # after every second song: the talking jingle, cycling English and Afrikaans
                ordered.append(ids[jingle_i[0] % len(ids)])
                jingle_i[0] += 1
        for s in ordered:
            # a song or playlist queued from the DJ app must play straight after this one
            try:
                if os.path.exists(NEXTQ) and os.path.getsize(NEXTQ) > 0:
                    break
            except Exception:
                pass
            # a program block starts and ends on time, even in the middle of a long batch
            try:
                if sched_active() != cur_sched:
                    break
            except Exception:
                pass
            # due story / program breaks pre-empt the music right away (not just between batches)
            try:
                for b in (take_break() or []):
                    blog("BREAK_ONAIR " + b)
                    _send_live(burst)
                    play_one(b)
            except Exception:
                pass
            _send_live(burst)
            if take_skip():
                continue
            path = os.path.join(MUSIC, s)
            try:
                with lock:
                    now_playing["song"] = pretty(s)
                    save_np()
                br = bitrate_of(path)
                amount = max(2048, int(br / 4))
                pos = 0
                f = open_media(s)
                try:
                    while True:
                        d = f.read(amount)
                        if not d:
                            break
                        pos = f.tell()
                        paced_broadcast(d, br, burst)
                        if pos % max(1, br // 8) < len(d):
                            if take_skip():
                                break
                            # a song or playlist queued from the DJ app takes over at once
                            try:
                                if os.path.exists(NEXTQ) and os.path.getsize(NEXTQ) > 0:
                                    break
                            except Exception:
                                pass
                            if live_files() or mic_recent():
                                f.close()
                                # hold the song while the microphone is in use, even in the
                                # gaps between voice chunks, so nothing leaks over the DJ
                                while mic_recent():
                                    _send_live(burst)
                                    if take_skip():
                                        break
                                    time.sleep(0.2)
                                _send_live(burst)
                                # the song carries on where it was: put its name back on screen
                                with lock:
                                    now_playing["song"] = pretty(s)
                                save_np()
                                f = open_media(s)
                                f.seek(pos)
                finally:
                    try:
                        f.close()
                    except Exception:
                        pass
            except Exception:
                continue


if __name__ == "__main__":
    save_np()
    threading.Thread(target=producer_loop, daemon=True).start()
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8000))), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
