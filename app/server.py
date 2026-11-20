"""Interpreter Web - server. Jalankan: python server.py [--port 8000]
Penerjemah : http://localhost:8000/console   (mic butuh localhost atau HTTPS)
Pendengar  : scan QR (alamat IP LAN) -> halaman pilih bahasa
Soundcard: edit config.json (enabled=true), cek device: python server.py --devices
Mode AI butuh env OPENAI_API_KEY. Untuk HTTPS taruh cert.pem + key.pem di folder ini.
Latar belakang (tanpa jendela): python server.py --background  (log di taiden.log, berhenti: POST /api/shutdown)."""
import argparse, asyncio, io, json, os, queue, socket, ssl, sys, threading, time, wave
import numpy as np, qrcode, qrcode.image.svg
from aiohttp import web, WSMsgType

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000
LANGS = {"ENG": "English", "IND": "Indonesian", "CHI": "Mandarin Chinese", "ARA": "Arabic", "JPN": "Japanese",
         "VIE": "Vietnamese", "KOR": "Korean", "THA": "Thai", "MAL": "Malay", "FRA": "French",
         "SPA": "Spanish", "GER": "German", "RUS": "Russian", "HIN": "Hindi"}
A = dict(stt="whisper-1", chat="gpt-4o-mini", tts="tts-1", voice="alloy")
FLOOR = "FLR"                                   # suara asli pembicara (original / floor)
CHANNELS = dict(FLR="Floor (Original)", **LANGS)
LCODE = {"ENG": "en", "IND": "id", "CHI": "zh", "ARA": "ar", "JPN": "ja", "VIE": "vi", "KOR": "ko", "THA": "th",
         "MAL": "ms", "FRA": "fr", "SPA": "es", "GER": "de", "RUS": "ru", "HIN": "hi"}
CODE_NAME = {v: LANGS[k] for k, v in LCODE.items()}
NAME_CODE = {LANGS[k]: v for k, v in LCODE.items()}
NATIVE = {"ENG": "English", "IND": "Indonesia", "CHI": "\u4e2d\u6587", "ARA": "\u0627\u0644\u0639\u0631\u0628\u064a\u0629", "JPN": "\u65e5\u672c\u8a9e",
          "VIE": "Ti\u1ebfng Vi\u1ec7t", "KOR": "\ud55c\uad6d\uc5b4", "THA": "\u0e44\u0e17\u0e22", "MAL": "Bahasa Melayu", "FRA": "Fran\u00e7ais",
          "SPA": "Espa\u00f1ol", "GER": "Deutsch", "RUS": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439", "HIN": "\u0939\u093f\u0928\u094d\u0926\u0940"}
BCP = {"ENG": "en-US", "IND": "id-ID", "CHI": "zh-CN", "ARA": "ar-SA", "JPN": "ja-JP", "VIE": "vi-VN", "KOR": "ko-KR",
       "THA": "th-TH", "MAL": "ms-MY", "FRA": "fr-FR", "SPA": "es-ES", "GER": "de-DE", "RUS": "ru-RU", "HIN": "hi-IN"}
DEF_CHANNELS = ["ENG", "IND", "CHI", "ARA", "JPN", "VIE"]       # channel yang tampil di pendengar (bisa diubah di Settings)
FLOOR_LANGS = ["en", "id", "auto"] + sorted(v for v in LCODE.values() if v not in ("en", "id"))
# Booth: interpreter/relay pasangan sumber -> tujuan. src = "FLR" (suara pembicara) atau channel lain; auto = relay AI otomatis.
DEF_BOOTHS = [{"id": 1, "name": "Booth 1", "src": "FLR", "tgt": "IND", "auto": True, "hp": None},
              {"id": 2, "name": "Booth 2", "src": "FLR", "tgt": "ARA", "auto": True, "hp": None},
              {"id": 3, "name": "Booth 3", "src": "FLR", "tgt": "VIE", "auto": True, "hp": None}]
HUMAN_HOLD = 4.0                                # detik: relay AI mengalah bila penerjemah manusia baru saja bicara di channel itu
listeners = {k: set() for k in CHANNELS}
monitors = {k: set() for k in CHANNELS}         # konsol yang memantau channel (tidak dihitung sebagai pendengar)
CAP_ON = True                                   # live caption
ROUTE = {}                                      # relay otomatis (turunan dari BOOTHS): {"CHI": "ENG"} = CHI menerjemahkan teks dari ENG; sumber bisa "FLR"
BOOTHS, ACTIVE, HUMAN_T, ERR_T, bout = [], list(DEF_CHANNELS), {}, {}, {}   # bout: {id booth: Out} headphone soundcard per booth
AUTO_ON = True                                  # saklar utama auto relay
BGMODE = False                                  # True = berjalan di latar belakang (log ke taiden.log)
FL = {"on": False, "stream": None, "err": "", "lv": 0.0,
      "cfg": {"source": "mic", "device": None, "channel": 0, "gain": 1.0, "lang": ""}}
LOOP = FQ = STT_LOCK = None
SEGS, CAP_LAST, BG = {}, {}, []
consoles, tasks, last, ctx, client = set(), set(), {}, [], None
CFG = os.path.join(HERE, "config.json")
DEF = {"soundcard": {"enabled": False, "samplerate": 48000, "outputs": {
    "ENG": {"device": None, "channel": 0}, "IND": {"device": None, "channel": 1}}}}
cards, card_err, devs = {}, [], {}


class Dev:
    """Satu device soundcard = satu stream multi-kolom (banyak bahasa bisa berbagi USB codec yang sama)."""
    def __init__(self, dev, sr, nch):
        import sounddevice as sd
        self.outs = []
        self.st = sd.OutputStream(samplerate=sr, device=dev, channels=nch, dtype="float32",
                                  blocksize=1024, callback=self.cb)
        self.st.start()

    def cb(self, out, frames, t, st):
        out.fill(0)
        for o in self.outs:
            while len(o.rem) < frames:
                try: o.rem = np.concatenate([o.rem, o.q.get_nowait()])
                except queue.Empty: break
            take, o.rem = o.rem[:frames], o.rem[frames:]
            out[:len(take), o.idx] += take
        np.clip(out, -1, 1, out=out)

    def close(self):
        try: self.st.stop(); self.st.close()
        except Exception: pass


class Out:
    """Satu bahasa -> satu kolom output (idx) pada sebuah Dev."""
    def __init__(self, dev, idx, sr):
        self.q, self.rem, self.sr, self.idx, self.dev = queue.Queue(), np.zeros(0, "float32"), sr, idx, dev
        dev.outs.append(self)

    def put(self, pcm):
        x = np.frombuffer(pcm, "<i2").astype("float32") / 32768
        if len(x) < 2: return
        n = int(len(x) * self.sr / SR)
        self.q.put(np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype("float32"))

    def close(self):
        self.dev.close()


def load_all():
    if not os.path.exists(CFG):
        with open(CFG, "w", encoding="utf-8") as f: json.dump(DEF, f, indent=2)
    with open(CFG, encoding="utf-8") as f: return json.load(f)


def save_all(upd):
    d = load_all(); d.update(upd)
    with open(CFG, "w", encoding="utf-8") as f: json.dump(d, f, indent=2, ensure_ascii=False)


def load_cfg():
    return load_all().get("soundcard", {})


def has_cycle(r):
    for k in r:
        seen, x = {k}, r[k]
        while x in r:
            if x in seen: return True
            seen.add(x); x = r[x]
    return False


def clean_booths(raw):
    """Rapikan daftar booth dari config / UI. Return (daftar, daftar pesan error)."""
    out, errs, ids = [], [], set()
    for i, b in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(b, dict): continue
        try: bid = int(b.get("id") or i + 1)
        except (TypeError, ValueError): bid = i + 1
        while bid in ids: bid += 1
        nm = (str(b.get("name") or "").strip()[:40]) or "Booth %d" % bid
        src, tgt = b.get("src") or FLOOR, b.get("tgt")
        if tgt not in LANGS or src not in CHANNELS:
            errs.append("%s: bahasa sumber/tujuan tidak valid" % nm); continue
        if src == tgt:
            errs.append("%s: sumber dan tujuan sama (%s)" % (nm, tgt)); continue
        hp, h = None, b.get("hp")
        if isinstance(h, dict) and h.get("device") not in (None, ""):
            try: hp = {"device": int(h["device"]), "channel": int(h.get("channel") or 0)}
            except (TypeError, ValueError): hp = None
        ids.add(bid)
        out.append({"id": bid, "name": nm, "src": src, "tgt": tgt, "auto": bool(b.get("auto", True)), "hp": hp})
    for k in {b["tgt"] for b in out if b["auto"]}:
        if sum(1 for b in out if b["auto"] and b["tgt"] == k) > 1:
            errs.append("Channel %s dipakai lebih dari satu booth Auto (satu channel hanya boleh punya satu sumber)" % k)
    if has_cycle(route_of(out)): errs.append("Relay membentuk lingkaran (A -> B -> A). Ubah salah satunya.")
    return out, errs


def route_of(booths):
    return {b["tgt"]: b["src"] for b in booths if b.get("auto")}


def booths_from_route(route):
    """Migrasi config lama (route) ke daftar booth."""
    return [{"id": i + 1, "name": "Booth %d" % (i + 1), "src": v, "tgt": k, "auto": True, "hp": None}
            for i, (k, v) in enumerate(sorted((route or {}).items())) if k in LANGS and v in CHANNELS and v != k]


def apply_settings():
    global CAP_ON, AUTO_ON
    d = load_all()
    CAP_ON = bool(d.get("captions", True))
    AUTO_ON = bool(d.get("autorelay", True))
    raw = d.get("booths")
    if raw is None: raw = booths_from_route(d["route"]) if d.get("route") else DEF_BOOTHS
    bl, errs = clean_booths(raw)
    if errs:
        print("Booth: ", "; ".join(errs)); bl = [b for b in bl if not b["auto"]]
    BOOTHS[:] = bl
    ROUTE.clear(); ROUTE.update(route_of(BOOTHS) if AUTO_ON else {})
    want = {c for c in (d.get("channels") or DEF_CHANNELS) if c in LANGS} | {b["tgt"] for b in BOOTHS}
    want |= {b["src"] for b in BOOTHS if b["src"] in LANGS}
    ACTIVE[:] = [c for c in LANGS if c in want]
    f = d.get("floor") or {}
    FL["cfg"] = {"source": f.get("source") if f.get("source") in ("mic", "display", "soundcard") else "mic",
                 "device": f.get("device"), "channel": int(f.get("channel") or 0), "gain": float(f.get("gain") or 1.0),
                 "lang": f.get("lang") if f.get("lang") in FLOOR_LANGS else ""}


def open_cards():
    for d in list(devs.values()): d.close()
    devs.clear(); cards.clear(); bout.clear(); card_err.clear()
    sc = load_cfg()
    if not sc.get("enabled"):
        print("Soundcard: nonaktif (atur di Settings konsol atau config.json)"); return
    sr, groups = sc.get("samplerate", 48000), {}
    for ch, c in sc.get("outputs", {}).items():
        if ch in CHANNELS and c.get("device") is not None:
            groups.setdefault(int(c["device"]), []).append((ch, int(c.get("channel", 0))))
    for b in BOOTHS:                                     # headphone interpreter per booth (suara yang didengar booth itu)
        if b.get("hp"): groups.setdefault(b["hp"]["device"], []).append(("B%d" % b["id"], b["hp"]["channel"]))
    for dev, items in groups.items():
        try:
            import sounddevice as sd
            mx = sd.query_devices(dev)["max_output_channels"]
            ok = []
            for ch, k in items:
                if k >= mx: card_err.append("%s: device %d hanya punya %d output, tidak ada Output %d" % (klabel(ch), dev, mx, k + 1))
                else: ok.append((ch, k))
            if not ok: continue
            d = Dev(dev, sr, max(k for _, k in ok) + 1); devs[dev] = d
            for ch, k in ok:
                o = Out(d, k, sr)
                if ch in CHANNELS: cards[ch] = o
                else: bout[int(ch[1:])] = o
                print("Soundcard %s -> device %d, Output %d" % (klabel(ch), dev, k + 1))
        except Exception as e:
            for ch, _ in items: card_err.append("%s: %s" % (klabel(ch), e))
            print("Soundcard device %s GAGAL: %s" % (dev, e))


def klabel(key):
    """Nama untuk kunci output: kode channel, atau 'Headphone <nama booth>'."""
    if key in CHANNELS: return key
    b = next((x for x in BOOTHS if "B%d" % x["id"] == key), None)
    return "Headphone " + (b["name"] if b else key)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def counts():
    m = json.dumps({"type": "counts", "data": {k: len(v) for k, v in listeners.items()}})
    for c in list(consoles):
        await c.send_str(m)


def booth_feed(ch, data):
    """Kirim audio channel ch ke headphone soundcard tiap booth yang sumbernya channel itu."""
    for b in BOOTHS:
        o = bout.get(b["id"])
        if o is not None and b["src"] == ch: o.put(data)


def preview_wanted():
    """Ada yang mendengarkan Floor lewat headphone (browser konsol / soundcard booth)?"""
    return bool(monitors[FLOOR]) or any(b["src"] == FLOOR and b["id"] in bout for b in BOOTHS)


async def fan(ch, data):
    if ch in cards: cards[ch].put(data)      # soundcard + WiFi sekaligus
    booth_feed(ch, data)
    for w in list(listeners[ch]) + list(monitors[ch]):
        try: await w.send_bytes(data)
        except Exception: listeners[ch].discard(w); monitors[ch].discard(w)


async def fan_mon(ch, data):
    """Hanya untuk interpreter (monitor browser + headphone booth), TIDAK ke pendengar. Dipakai saat Floor OFF."""
    booth_feed(ch, data)
    for w in list(monitors[ch]):
        try: await w.send_bytes(data)
        except Exception: monitors[ch].discard(w)


async def fan_text(ch, text, speak=True, via=None):
    m = json.dumps({"type": "tx", "text": text, "speak": speak, "via": via})
    for w in list(listeners[ch]):
        try: await w.send_str(m)
        except Exception: listeners[ch].discard(w)


def gemini_post(models, body, tries=3):
    """POST ke Gemini; ulangi saat 503/429/500 dan pindah ke model cadangan bila perlu."""
    import urllib.request, urllib.error, time
    key, err = os.environ["GEMINI_API_KEY"], None
    for model in models:
        for i in range(tries):
            req = urllib.request.Request(
                "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
            except urllib.error.HTTPError as e:
                err = "Gemini %s (%s): %s" % (e.code, model, e.read()[:200].decode(errors="ignore"))
                if e.code == 404: break          # model tidak ada -> coba model berikutnya
                if e.code not in (429, 500, 503): raise RuntimeError(err)
                print("Retry %d/%d -> %s" % (i + 1, tries, err.splitlines()[0]))
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(err)


def gemini_tts(text):
    """Suara GRATIS dari Gemini TTS (kunci API sama). Mengembalikan PCM 16 kHz int16, atau None kalau gagal."""
    import base64
    models = [os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
              os.environ.get("GEMINI_TTS_FALLBACK", "gemini-3.8-flash-lite-tts")]
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": os.environ.get("GEMINI_VOICE", "Kore")}}}}}
    try:
        res = gemini_post(models, body, tries=2)
        for p in res["candidates"][0]["content"]["parts"]:
            d = p.get("inlineData") or p.get("inline_data")
            if d:
                y = np.frombuffer(base64.b64decode(d["data"]), "<i2").astype("float32")
                import re
                m = re.search(r"rate=(\d+)", d.get("mimeType") or d.get("mime_type") or "")
                n = int(len(y) * SR / (int(m.group(1)) if m else 24000))
                return np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y).astype("<i2").tobytes()
        print("Gemini TTS: tidak ada audio di respons")
    except Exception as e:
        print("Gemini TTS gagal:", e)
    return None


def translate_gemini(lang, pcm):
    """Versi GRATIS: Google Gemini (set env GEMINI_API_KEY). Suara -> teks -> terjemahan, 1 panggilan."""
    import base64
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
    prompt = ("You are a simultaneous conference interpreter. The audio is Indonesian speech. "
              "Transcribe it, then translate it into %s. It may be a fragment: translate faithfully, "
              "do not add or complete. If there is no clear speech, use empty strings. "
              "Reply ONLY with JSON: {\"src\": \"<Indonesian transcript>\", \"tr\": \"<translation>\"}" % lang)
    body = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {
                "mime_type": "audio/wav", "data": base64.b64encode(buf.getvalue()).decode()}}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    models = [os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
              os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")]
    res = gemini_post(models, body)
    parts = res["candidates"][0]["content"]["parts"]
    d = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
    src, tr = (d.get("src") or "").strip(), (d.get("tr") or "").strip()
    if len(src) < 2: return None, None, None
    tr = src if lang == "Indonesian" else tr
    return src, tr, (gemini_tts(tr) if tr else None)


def translate(lang, pcm):
    global client
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
    buf.name = "s.wav"; buf.seek(0)
    src = client.audio.transcriptions.create(model=A["stt"], file=buf, language="id",
                                             prompt=" ".join(ctx[-2:])).text.strip()
    if len(src) < 2:
        return None, None, None
    ctx.append(src); del ctx[:-4]
    if lang == "Indonesian":
        tr = src
    else:
        sysmsg = ("You are a simultaneous conference interpreter. Translate the user's Indonesian speech into %s. "
                  "It may be a fragment: translate faithfully, do not add or complete. Output only the translation." % lang)
        tr = client.chat.completions.create(model=A["chat"], messages=[
            {"role": "system", "content": sysmsg}, {"role": "user", "content": src}]).choices[0].message.content.strip()
    r = client.audio.speech.create(model=A["tts"], voice=A["voice"], input=tr, response_format="pcm")
    y = np.frombuffer(r.content, "<i2").astype("float32")
    n = int(len(y) * SR / 24000)
    y = np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y).astype("<i2")
    return src, tr, y.tobytes()


_whisper = None
_tts_lock = threading.Lock()
_wlock = threading.Lock()                        # faster-whisper tidak dipakai paralel


def whisper_model():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="auto", compute_type="int8")
    return _whisper


VOICE_TAGS = {"id": ("indonesia", "id-id", "idid"), "en": ("english", "en-us", "enus", "en-gb"),
              "vi": ("vietnam", "vi-vn", "vivn"), "ar": ("arabic", "ar-sa", "arsa", "ar-eg", "areg"),
              "zh": ("chinese", "zh-cn", "zhcn", "mandarin"), "ja": ("japanese", "ja-jp", "jajp"),
              "ko": ("korean", "ko-kr", "kokr"), "th": ("thai", "th-th", "thth"), "ms": ("malay", "ms-my", "msmy"),
              "fr": ("french", "fr-fr", "frfr"), "es": ("spanish", "es-es", "eses", "es-mx", "esmx"),
              "de": ("german", "de-de", "dede"), "ru": ("russian", "ru-ru", "ruru"), "hi": ("hindi", "hi-in", "hiin")}


def local_tts(text, code):
    """TTS OFFLINE (suara Windows via pyttsx3). code = id / en / vi / ar / ...
    Mengembalikan PCM 16 kHz int16, atau None kalau gagal / suara bahasa itu tidak terpasang di Windows
    (-> cadangan: suara browser pendengar)."""
    if not text:
        return None
    f = None
    try:
        import pyttsx3, tempfile
        with _tts_lock:                              # SAPI tidak aman dipakai paralel
            try:
                import pythoncom; pythoncom.CoInitialize()   # perlu di thread non-utama (Windows)
            except Exception:
                pass
            e = pyttsx3.init()
            tag, found = VOICE_TAGS.get(code, ()), False
            for v in e.getProperty("voices"):
                if any(t in (v.id + " " + (v.name or "")).lower() for t in tag):
                    e.setProperty("voice", v.id); found = True; break
            if not found and code not in ("id", "en"):       # jangan bacakan bahasa lain dengan suara Inggris
                try: e.stop()
                except Exception: pass
                return None
            e.setProperty("rate", 170)
            fd, f = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            e.save_to_file(text, f); e.runAndWait()
            try: e.stop()
            except Exception: pass
        with wave.open(f, "rb") as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2 or not raw:
            print("TTS lokal: format WAV tidak didukung"); return None
        y = np.frombuffer(raw, "<i2").astype("float32")
        if ch > 1: y = y[::ch]
        if len(y) < 2: return None
        n = int(len(y) * SR / sr)
        return np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y).astype("<i2").tobytes()
    except Exception as ex:
        print("TTS lokal gagal:", ex)
        return None
    finally:
        if f and os.path.exists(f):
            try: os.remove(f)
            except Exception: pass


def argos_tr(text, s, t):
    """Terjemahan OFFLINE lewat Argos (pasangan lain dilewatkan via English bila paketnya terpasang)."""
    if s == t: return text
    import argostranslate.translate as at
    try:
        return at.translate(text, s, t).strip()
    except Exception as e:
        raise RuntimeError("model offline %s -> %s belum terpasang (%s). Pasang paket Argos-nya atau pakai mode Gemini/OpenAI" % (s, t, e))


def translate_local(lang, pcm):
    """OFFLINE: faster-whisper (suara->teks) + Argos (terjemahan) + pyttsx3 (suara). Tanpa kuota, tanpa biaya.
    SRC_LANG = id (default) / en / auto -> bahasa yang diucapkan pembicara."""
    x = np.frombuffer(pcm, "<i2").astype("float32") / 32768
    s = os.environ.get("SRC_LANG", "id")
    with _wlock:
        segs, info = whisper_model().transcribe(x, language=None if s == "auto" else s,
                                                vad_filter=True, beam_size=1)
        src = " ".join(g.text.strip() for g in segs).strip()
    if len(src) < 2:
        return None, None, None
    if s == "auto":
        s = info.language if info.language in LCODE.values() else "id"
    t = NAME_CODE.get(lang, "en")
    if s == t:
        return src, src, local_tts(src, t)
    tr = argos_tr(src, s, t)
    return src, tr, local_tts(tr, t)      # None = suara dari browser pendengar


async def ai(ch, pcm, ws):
    prev, me = last.get(ch), asyncio.get_running_loop().create_future()
    last[ch] = me
    try:
        fn = (translate_local if os.environ.get("AI_MODE") == "local"
              else translate_gemini if os.environ.get("GEMINI_API_KEY") else translate)
        src, tr, y = await asyncio.to_thread(fn, LANGS[ch], pcm)
        if prev: await prev                      # putar berurutan
        if src:
            await ws.send_str(json.dumps({"type": "log", "text": "ID: %s\n%s: %s" % (src, ch, tr)}))
            if y is None: await fan_text(ch, tr)          # cadangan: suara browser pendengar
            else:
                await fan_text(ch, tr, speak=False)        # teks saja (subtitle)
                for i in range(0, len(y), 3200): await fan(ch, y[i:i + 3200])
            await relay_from(ch, tr, LCODE[ch])                 # channel lain yang me-relay dari channel ini
    except Exception as e:
        await ws.send_str(json.dumps({"type": "log", "text": "AI ERROR: %s (cek GEMINI_API_KEY / OPENAI_API_KEY)" % e}))
    finally:
        me.set_result(None)


async def ws_console(req):
    ws = web.WebSocketResponse(heartbeat=20); await ws.prepare(req)
    consoles.add(ws); await counts()
    await ws.send_str(json.dumps({"type": "log", "text": "Pendengar: " + req.app["url"]}))
    if cards: await ws.send_str(json.dumps({"type": "log", "text": "Soundcard aktif: " + ", ".join(cards)}))
    async for m in ws:
        if m.type == WSMsgType.TEXT:
            try: d = json.loads(m.data)
            except Exception: continue
            if d.get("type") == "floor": FL["on"] = bool(d.get("on"))
            continue
        if m.type != WSMsgType.BINARY or len(m.data) < 5: continue
        mode, ch, pcm = m.data[:1], m.data[1:4].decode(errors="ignore"), m.data[4:]
        if ch == FLOOR:                                  # floor dari mic/line-in/audio sistem di browser konsol
            if mode == b"H" and FL["on"] and FL["cfg"]["source"] != "soundcard": await ingest(ch, pcm)
            continue
        if ch not in LANGS: continue
        HUMAN_T[ch] = time.monotonic()                   # penerjemah manusia aktif -> relay AI channel ini mengalah
        if mode == b"H": await ingest(ch, pcm)           # suara penerjemah manusia + caption/relay
        else:
            t = asyncio.create_task(ai(ch, pcm, ws)); tasks.add(t); t.add_done_callback(tasks.discard)
    consoles.discard(ws)
    if not consoles: FL["on"] = False                    # konsol terakhir menutup -> Floor mati
    return ws


async def ws_listen(req):
    ws = web.WebSocketResponse(heartbeat=20); await ws.prepare(req)
    ch = req.query.get("ch", "")
    if ch in listeners:
        if req.query.get("mon") == "1":                  # pemantau dari konsol (headphone penerjemah)
            monitors[ch].add(ws)
            async for _ in ws: pass
            monitors[ch].discard(ws)
        else:
            listeners[ch].add(ws); await counts()
            async for _ in ws: pass
            listeners[ch].discard(ws); await counts()
    return ws


async def qr(req):
    b = io.BytesIO()
    qrcode.make(req.app["url"], image_factory=qrcode.image.svg.SvgPathImage).save(b)
    return web.Response(body=b.getvalue(), content_type="image/svg+xml")


async def api_out_get(req):
    import sounddevice as sd
    lst = []
    try:
        hs = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0:
                lst.append({"id": i, "name": "%s [%s]" % (d["name"], hs[d["hostapi"]]["name"]),
                            "out": d["max_output_channels"]})
    except Exception as e:
        print("Daftar device gagal:", e)
    return web.json_response({"devices": lst, "langs": CHANNELS, "config": load_cfg(),
                              "active": list(cards), "booth_active": list(bout), "errors": list(card_err)})


async def api_out_set(req):
    d = await req.json()
    outs = {k: {"device": int(v["device"]), "channel": int(v.get("channel", 0))}
            for k, v in (d.get("outputs") or {}).items() if k in CHANNELS and v.get("device") is not None}
    sc = {"enabled": bool(d.get("enabled")), "samplerate": int(d.get("samplerate", 48000)), "outputs": outs}
    save_all({"soundcard": sc})
    await asyncio.to_thread(open_cards)
    return web.json_response({"ok": True, "active": list(cards), "booth_active": list(bout), "errors": list(card_err)})


async def api_out_test(req):
    d = await req.json()
    o = bout.get(int(d["booth"])) if d.get("booth") not in (None, "") else cards.get(d.get("ch"))
    if o is None: return web.json_response({"ok": False})
    t = np.arange(SR) / SR
    o.put((np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype("<i2").tobytes())
    return web.json_response({"ok": True})


# ===================== Floor, Live Caption, Relay =====================
def ai_mode():
    return "local" if os.environ.get("AI_MODE") == "local" else "gemini" if os.environ.get("GEMINI_API_KEY") else "openai"


def openai_client():
    global client
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    return client


def wav_bytes(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
    buf.seek(0)
    return buf


def stt(pcm, code):
    """Suara -> teks (caption & relay). code = id/en/zh/ar/ja, atau None = deteksi otomatis. Return (teks, kode bahasa)."""
    m = ai_mode()
    if m == "local":
        x = np.frombuffer(pcm, "<i2").astype("float32") / 32768
        with _wlock:
            sg, info = whisper_model().transcribe(x, language=code, vad_filter=True, beam_size=1)
            text = " ".join(g.text.strip() for g in sg).strip()
        return text, (code or info.language)
    if m == "gemini":
        import base64
        hint = (" The language is %s." % CODE_NAME[code]) if code in CODE_NAME else ""
        body = {"contents": [{"parts": [
            {"text": "Transcribe this speech verbatim, without translating.%s If there is no clear speech, use an empty string. "
                     "Reply ONLY with JSON: {\"text\": \"<transcript>\", \"lang\": \"<ISO 639-1 code>\"}" % hint},
            {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav_bytes(pcm).getvalue()).decode()}}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
        models = [os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"), os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")]
        parts = gemini_post(models, body)["candidates"][0]["content"]["parts"]
        d = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
        return (d.get("text") or "").strip(), (code or (d.get("lang") or "").strip().lower() or None)
    f = wav_bytes(pcm); f.name = "s.wav"
    kw = {"language": code} if code else {}
    return openai_client().audio.transcriptions.create(model=A["stt"], file=f, **kw).text.strip(), code


def gemini_text(text, lang):
    body = {"contents": [{"parts": [{"text":
            "You are a simultaneous conference interpreter. Translate the text into %s faithfully. It may be a fragment: "
            "do not add or complete. Reply ONLY with JSON: {\"tr\": \"<translation>\"}\n\nText: %s" % (lang, text)}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    models = [os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"), os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")]
    parts = gemini_post(models, body)["candidates"][0]["content"]["parts"]
    return (json.loads("".join(p.get("text", "") for p in parts if not p.get("thought"))).get("tr") or "").strip()


def speak_pcm(text, code):
    """Teks -> suara PCM 16 kHz int16, atau None (-> cadangan: suara browser pendengar)."""
    m = ai_mode()
    if m == "local": return local_tts(text, code)
    if m == "gemini": return gemini_tts(text)
    r = openai_client().audio.speech.create(model=A["tts"], voice=A["voice"], input=text, response_format="pcm")
    y = np.frombuffer(r.content, "<i2").astype("float32")
    return np.interp(np.linspace(0, len(y) - 1, int(len(y) * SR / 24000)), np.arange(len(y)), y).astype("<i2").tobytes()


def translate_text(tgt, text, scode):
    """Relay: teks (bahasa scode) -> channel tgt. Return (terjemahan, PCM atau None)."""
    tcode = LCODE[tgt]
    if scode == tcode:
        tr = text
    else:
        m = ai_mode()
        if m == "local":
            tr = argos_tr(text, scode, tcode)
        elif m == "gemini":
            tr = gemini_text(text, LANGS[tgt])
        else:
            tr = openai_client().chat.completions.create(model=A["chat"], messages=[
                {"role": "system", "content": "You are a simultaneous conference interpreter. Translate the user's text into %s faithfully. "
                                              "It may be a fragment: do not add or complete. Output only the translation." % LANGS[tgt]},
                {"role": "user", "content": text}]).choices[0].message.content.strip()
    return tr, (speak_pcm(tr, tcode) if tr else None)


class Seg:
    """Pemotong ucapan sederhana (berbasis energi) untuk caption/relay."""
    def __init__(s): s.buf, s.n, s.sil, s.had, s.t = [], 0, 0, False, time.monotonic()

    def feed(s, pcm):
        pcm = pcm[:len(pcm) // 2 * 2]
        x = np.frombuffer(pcm, "<i2")
        if not len(x): return None
        s.t = time.monotonic()
        rms = float(np.sqrt(np.mean((x.astype("float32") / 32768) ** 2)))
        s.buf.append(pcm); s.n += len(x)
        if rms > 0.012: s.had, s.sil = True, 0
        else: s.sil += len(x)
        d = s.n / SR
        if s.had and ((s.sil / SR >= 0.6 and d >= 1.0) or d >= 8): return s.cut()
        if not s.had and d > 1.0: s.buf, s.n, s.sil = [], 0, 0
        return None

    def cut(s):
        out = b"".join(s.buf); s.buf, s.n, s.sil, s.had = [], 0, 0, False
        return out


def spawn(coro):
    t = asyncio.ensure_future(coro); tasks.add(t); t.add_done_callback(tasks.discard)
    return t


async def clog(text):
    m = json.dumps({"type": "log", "text": text})
    for c in list(consoles):
        try: await c.send_str(m)
        except Exception: consoles.discard(c)


def floor_lang():
    """Bahasa pembicara di Floor: pilihan di Settings, kalau kosong ikut SRC_LANG di settings.env."""
    return FL["cfg"].get("lang") or os.environ.get("SRC_LANG", "id")


def src_code(ch):
    if ch == FLOOR:
        s = floor_lang()
        return None if s == "auto" else s
    return LCODE[ch]


def needs_stt(ch):
    return CAP_ON or ch in ROUTE.values()


async def ingest(ch, pcm):
    """Audio masuk sebuah channel (floor / penerjemah manusia): kirim ke pendengar + siapkan caption & relay."""
    await fan(ch, pcm)
    if needs_stt(ch):
        out = SEGS.setdefault(ch, Seg()).feed(pcm)
        if out: spawn(caption(ch, out))


async def caption(ch, pcm):
    global STT_LOCK
    if STT_LOCK is None: STT_LOCK = asyncio.Lock()
    prev, me = CAP_LAST.get(ch), asyncio.get_running_loop().create_future()
    CAP_LAST[ch] = me
    try:
        async with STT_LOCK:
            text, code = await asyncio.to_thread(stt, pcm, src_code(ch))
        if prev: await prev
        if len(text) >= 2:
            if CAP_ON: await fan_text(ch, text, speak=False)
            await relay_from(ch, text, code or src_code(ch) or "id")
    except Exception as e:
        await clog("CAPTION ERROR %s: %s" % (ch, e))
    finally:
        me.set_result(None)


async def relay_from(src, text, scode, depth=0):
    """Teruskan teks dari channel src ke setiap channel yang diatur me-relay dari src."""
    if depth > 4: return
    for tgt, s in list(ROUTE.items()):
        if s == src and tgt != src: spawn(relay_one(tgt, src, text, scode, depth))


async def clog_once(key, text, every=30):
    """Log yang sama tidak diulang tiap segmen ucapan (mis. model offline belum terpasang)."""
    now = time.monotonic()
    if now - ERR_T.get(key, -1e9) >= every:
        ERR_T[key] = now; await clog(text)


async def relay_one(tgt, src, text, scode, depth):
    prev, me = last.get(tgt), asyncio.get_running_loop().create_future()
    last[tgt] = me
    try:
        if time.monotonic() - HUMAN_T.get(tgt, -1e9) < HUMAN_HOLD:
            await clog_once(("h", tgt), "RELAY %s -> %s dilewati: penerjemah manusia sedang bicara di %s" % (src, tgt, tgt), 15)
            return
        tr, y = await asyncio.to_thread(translate_text, tgt, text, scode)
        if prev: await prev
        if tr:
            await clog("RELAY %s -> %s: %s" % (src, tgt, tr))
            if y is None: await fan_text(tgt, tr, via=src)         # cadangan: suara browser pendengar
            else:
                await fan_text(tgt, tr, speak=False, via=src)
                for i in range(0, len(y), 3200): await fan(tgt, y[i:i + 3200])
            await relay_from(tgt, tr, LCODE[tgt], depth + 1)       # relay berantai
    except Exception as e:
        await clog_once(("e", tgt, str(e)[:80]), "RELAY ERROR %s -> %s: %s" % (src, tgt, e))
    finally:
        me.set_result(None)


# ---- Floor lewat soundcard (device + nomor input), dijalankan di server ----
def floor_push(pcm):
    try: FQ.put_nowait(pcm)
    except asyncio.QueueFull: pass


def close_floor():
    s, FL["stream"] = FL["stream"], None
    if s is not None:
        try: s.stop(); s.close()
        except Exception: pass


def open_floor():
    close_floor(); FL["err"] = ""
    c = FL["cfg"]
    if c["source"] != "soundcard" or c.get("device") is None: return
    try:
        import sounddevice as sd
        dev, k = int(c["device"]), int(c.get("channel", 0))
        info = sd.query_devices(dev)
        if k >= info["max_input_channels"]:
            raise RuntimeError("device %d hanya punya %d input, tidak ada Input %d" % (dev, info["max_input_channels"], k + 1))
        sr, g = int(info["default_samplerate"]), float(c.get("gain", 1.0))

        def cb(indata, frames, t, st):
            x = indata[:, k].astype("float32") * g
            if not len(x): return
            FL["lv"] = float(np.sqrt(np.mean(x * x)))
            if LOOP is None or not (FL["on"] or preview_wanted()): return
            r = sr // SR
            if sr % SR == 0 and r > 1: y = x[:len(x) // r * r].reshape(-1, r).mean(1)
            else: y = np.interp(np.linspace(0, len(x) - 1, max(2, int(len(x) * SR / sr))), np.arange(len(x)), x)
            try: LOOP.call_soon_threadsafe(floor_push, (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
            except RuntimeError: pass

        s = sd.InputStream(samplerate=sr, device=dev, channels=k + 1, dtype="float32", blocksize=int(sr * 0.1), callback=cb)
        s.start(); FL["stream"] = s
        print("Floor input -> device %d, Input %d (%d Hz)" % (dev, k + 1, sr))
    except Exception as e:
        FL["err"] = str(e); print("Floor input GAGAL:", e)


async def floor_pump():
    while True:
        pcm = await FQ.get()
        try:
            if FL["on"]: await ingest(FLOOR, pcm)
            else: await fan_mon(FLOOR, pcm)          # Floor OFF: hanya didengar interpreter (headphone), tidak ke pendengar
        except Exception as e: print("floor:", e)


async def floor_meter():
    while True:
        await asyncio.sleep(0.12)
        if FL["stream"] is not None and consoles:
            m = json.dumps({"type": "floor", "lv": FL["lv"], "on": FL["on"]})
            for c in list(consoles):
                try: await c.send_str(m)
                except Exception: consoles.discard(c)


async def seg_flusher():
    """Ucapan yang berhenti tanpa jeda cukup (mis. tombol bicara dilepas) tetap diproses."""
    while True:
        await asyncio.sleep(0.4)
        for ch, s in list(SEGS.items()):
            if s.had and s.n / SR >= 0.5 and time.monotonic() - s.t > 1.0: spawn(caption(ch, s.cut()))


async def start_bg(app):
    global LOOP, FQ
    LOOP, FQ = asyncio.get_running_loop(), asyncio.Queue(maxsize=300)
    BG[:] = [asyncio.ensure_future(f()) for f in (floor_pump, floor_meter, seg_flusher)]
    await asyncio.to_thread(open_floor)


async def stop_bg(app):
    for t in BG: t.cancel()
    close_floor()


async def api_get(req):
    inputs = []
    try:
        import sounddevice as sd
        hs = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                inputs.append({"id": i, "name": "%s [%s]" % (d["name"], hs[d["hostapi"]]["name"]), "in": d["max_input_channels"]})
    except Exception as e:
        print("Daftar input gagal:", e)
    return web.json_response({"inputs": inputs, "floor": FL["cfg"], "floor_err": FL["err"], "floor_active": FL["stream"] is not None,
                              "captions": CAP_ON, "route": ROUTE, "mode": ai_mode(), "src_lang": floor_lang(),
                              "booths": BOOTHS, "autorelay": AUTO_ON, "channels": ACTIVE, "langs": LANGS, "native": NATIVE,
                              "floor_langs": FLOOR_LANGS, "background": BGMODE, "url": req.app["url"],
                              "booth_active": list(bout), "card_errors": list(card_err)})


async def api_set(req):
    d = await req.json(); upd = {}
    if "captions" in d: upd["captions"] = bool(d["captions"])
    if "autorelay" in d: upd["autorelay"] = bool(d["autorelay"])
    if "channels" in d: upd["channels"] = [c for c in (d["channels"] or []) if c in LANGS]
    if "booths" in d:
        bl, errs = clean_booths(d["booths"])
        if errs: return web.json_response({"ok": False, "error": "\n".join(errs)}, status=400)
        upd["booths"] = bl
    if "floor" in d:
        f = d["floor"] or {}
        upd["floor"] = {"source": f.get("source") if f.get("source") in ("mic", "display", "soundcard") else "mic",
                        "device": int(f["device"]) if f.get("device") not in (None, "") else None,
                        "channel": int(f.get("channel") or 0), "gain": max(.25, min(4.0, float(f.get("gain") or 1.0))),
                        "lang": f.get("lang") if f.get("lang") in FLOOR_LANGS else ""}
    save_all(upd); apply_settings()
    if "floor" in d: await asyncio.to_thread(open_floor)
    if "booths" in d: await asyncio.to_thread(open_cards)         # headphone booth ikut dibuka ulang
    return web.json_response({"ok": True, "floor": FL["cfg"], "floor_err": FL["err"], "floor_active": FL["stream"] is not None,
                              "captions": CAP_ON, "route": ROUTE, "booths": BOOTHS, "autorelay": AUTO_ON, "channels": ACTIVE,
                              "src_lang": floor_lang(), "booth_active": list(bout), "card_errors": list(card_err)})


async def api_channels(req):
    """Daftar channel untuk halaman pendengar (hanya yang aktif)."""
    ch = [{"c": FLOOR, "n": "Floor", "e": "Original \u00b7 suara pembicara", "v": ""}]
    ch += [{"c": c, "n": NATIVE[c], "e": "Bahasa Indonesia" if c == "IND" else LANGS[c], "v": BCP[c]} for c in LANGS if c in ACTIVE]
    return web.json_response({"channels": ch})


async def api_shutdown(req):
    """Hentikan server (mode latar belakang tidak punya jendela untuk ditutup). Hanya dari komputer ini + header khusus."""
    if req.remote not in ("127.0.0.1", "::1") or req.headers.get("X-TAIDEN") != "1":
        return web.json_response({"ok": False, "error": "ditolak"}, status=403)

    def bye():
        try: close_floor()
        except Exception: pass
        for dv in list(devs.values()): dv.close()
        print("TAIDEN dihentikan (shutdown)"); sys.stdout.flush()
        os._exit(0)                                     # kode 0 = berhenti normal, watchdog tidak menyalakan ulang
    threading.Timer(0.4, bye).start()
    return web.json_response({"ok": True})


def page(name):
    return lambda req: web.FileResponse(os.path.join(HERE, "static", name))


def setup_background():
    """Latar belakang: tanpa jendela. pythonw tidak punya stdout, jadi semua output dialihkan ke taiden.log."""
    global BGMODE
    BGMODE = True
    lp = os.path.join(HERE, "taiden.log")
    try:
        if os.path.exists(lp) and os.path.getsize(lp) > 2_000_000: os.replace(lp, lp + ".1")
    except OSError: pass
    f = open(lp, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = f
    print("\n===== TAIDEN mulai %s (pid %d) =====" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))


def already_running(port):
    s = socket.socket(); s.settimeout(0.6)
    try: s.connect(("127.0.0.1", port)); return True
    except OSError: return False
    finally: s.close()


async def open_browser(app):
    import webbrowser
    asyncio.get_running_loop().call_later(1.2, webbrowser.open, app["console_url"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8000); ap.add_argument("--devices", action="store_true")
    ap.add_argument("--background", action="store_true", help="jalan tanpa jendela, log ke taiden.log")
    ap.add_argument("--no-browser", action="store_true", help="jangan buka browser otomatis"); a = ap.parse_args()
    if a.devices:
        import sounddevice as sd; print(sd.query_devices()); raise SystemExit
    if a.background or sys.stdout is None: setup_background()
    cert, key = os.path.join(HERE, "cert.pem"), os.path.join(HERE, "key.pem")
    curl = "%s://localhost:%d/console" % ("https" if os.path.exists(cert) and os.path.exists(key) else "http", a.port)
    if already_running(a.port):                          # satu instance saja; peluncuran kedua hanya membuka konsol
        print("TAIDEN sudah berjalan di port %d" % a.port)
        if not a.no_browser:
            import webbrowser; webbrowser.open(curl)
        raise SystemExit(0)
    sslc = None
    if os.path.exists(cert) and os.path.exists(key):
        sslc = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sslc.load_cert_chain(cert, key)
    url = "%s://%s:%d/" % ("https" if sslc else "http", lan_ip(), a.port)
    app = web.Application(); app["url"] = url; app["console_url"] = curl
    app.on_startup.append(start_bg); app.on_cleanup.append(stop_bg)
    if not a.no_browser: app.on_startup.append(open_browser)
    app.add_routes([web.get("/", page("listen.html")), web.get("/console", page("console.html")),
                    web.get("/output", lambda r: web.HTTPFound("/console#set")),
                    web.get("/api/output", api_out_get), web.post("/api/output", api_out_set),
                    web.post("/api/output/test", api_out_test), web.get("/api/settings", api_get), web.post("/api/settings", api_set),
                    web.get("/api/channels", api_channels), web.post("/api/shutdown", api_shutdown), web.get("/qr.svg", qr), web.get("/ws/console", ws_console), web.get("/ws/listen", ws_listen)])
    apply_settings(); open_cards()
    print("\nPendengar :", url, "\nPenerjemah: http://localhost:%d/console" % a.port,
          "\nOutput    : http://localhost:%d/output\n" % a.port)
    if not BGMODE:
        q = qrcode.QRCode(); q.add_data(url); q.print_ascii(invert=True)
    web.run_app(app, host="0.0.0.0", port=a.port, ssl_context=sslc, print=None)
