import base64
import json
import os
import re
import subprocess
import threading
import time
import uuid

import numpy as np
import soundfile as sf
import webview

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_RATE = 24000
SETTINGS = os.path.join(HERE, "settings.json")
HISTORY = os.path.join(HERE, "history.json")
CACHE = os.path.join(HERE, "cache")
MAX_HISTORY = 20
REPO = "hexgrad/Kokoro-82M"

VOICES = [
    ("af_heart", "Heart", "American", "Female"),
    ("af_bella", "Bella", "American", "Female"),
    ("af_nicole", "Nicole", "American", "Female"),
    ("af_sarah", "Sarah", "American", "Female"),
    ("af_sky", "Sky", "American", "Female"),
    ("am_adam", "Adam", "American", "Male"),
    ("am_michael", "Michael", "American", "Male"),
    ("bf_emma", "Emma", "British", "Female"),
    ("bf_isabella", "Isabella", "British", "Female"),
    ("bm_george", "George", "British", "Male"),
    ("bm_lewis", "Lewis", "British", "Male"),
    ("ef_dora", "Dora", "Spanish", "Female"),
    ("em_alex", "Alex", "Spanish", "Male"),
    ("em_santa", "Santa", "Spanish", "Male"),
    ("ff_siwis", "Siwis", "French", "Female"),
    ("hf_alpha", "Alpha", "Hindi", "Female"),
    ("hf_beta", "Beta", "Hindi", "Female"),
    ("hm_omega", "Omega", "Hindi", "Male"),
    ("hm_psi", "Psi", "Hindi", "Male"),
    ("if_sara", "Sara", "Italian", "Female"),
    ("im_nicola", "Nicola", "Italian", "Male"),
    ("pf_dora", "Dora", "Portuguese (BR)", "Female"),
    ("pm_alex", "Alex", "Portuguese (BR)", "Male"),
    ("pm_santa", "Santa", "Portuguese (BR)", "Male"),
]
VOICE_NAMES = {i: n for i, n, _, _ in VOICES}


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def model_cached():
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(REPO, "kokoro-v1_0.pth"), str)
    except Exception:
        return False


def mps_available():
    try:
        import torch
        return torch.backends.mps.is_available()
    except Exception:
        return False


class Cancelled(Exception):
    pass


WORD = re.compile(r"\S+")
HAS_WORD_CHAR = re.compile(r"\w")


def align_chunk(text, cursor, res, offset, dur):
    """Map one Kokoro result chunk to word timings in the original text.

    English results carry per-token timestamps. Other languages don't, so the
    chunk's duration is shared out across its words by length instead.
    """
    words = []
    toks = [t for t in (res.tokens or []) if t.start_ts is not None and t.end_ts is not None]
    if toks:
        for t in toks:
            i = text.find(t.text, cursor)
            if i < 0:
                continue  # token was normalised by the G2P; skip it
            cursor = i + len(t.text)
            if HAS_WORD_CHAR.search(t.text):
                words.append([round(offset + t.start_ts, 3), round(offset + t.end_ts, 3), i, cursor])
        return words, cursor

    g = (res.graphemes or "").strip()
    start = text.find(g, cursor) if g else -1
    if start < 0:
        start = cursor
    end = min(len(text), start + len(g))
    spans = [m.span() for m in WORD.finditer(text, start, end)]
    weight = sum(b - a + 1 for a, b in spans) or 1
    t = offset
    for a, b in spans:
        step = dur * (b - a + 1) / weight
        words.append([round(t, 3), round(t + step, 3), a, b])
        t += step
    return words, max(end, cursor)


class Api:
    def __init__(self):
        os.makedirs(CACHE, exist_ok=True)
        self.pipelines = {}
        self.window = None
        self.cancel_flag = threading.Event()
        self.busy = False
        self.lock = threading.Lock()
        self.history = [h for h in read_json(HISTORY, []) if os.path.exists(self._audio_path(h["id"]))]

    # ---- helpers
    def _audio_path(self, id_):
        return os.path.join(CACHE, f"{id_}.wav")

    def _settings(self):
        return read_json(SETTINGS, {})

    def _set(self, **kw):
        s = self._settings()
        s.update(kw)
        write_json(SETTINGS, s)

    def _save_history(self):
        for h in self.history[MAX_HISTORY:]:
            try:
                os.remove(self._audio_path(h["id"]))
            except OSError:
                pass
        self.history = self.history[:MAX_HISTORY]
        write_json(HISTORY, self.history)

    def _js(self, fn, payload):
        if self.window:
            self.window.evaluate_js(f"{fn}({json.dumps(payload)})")

    def _device(self):
        pref = self._settings().get("device", "auto")
        return "mps" if pref == "auto" and mps_available() else "cpu"

    def _pipeline(self, lang, device):
        key = (lang, device)
        if key not in self.pipelines:
            from kokoro import KPipeline
            self.pipelines[key] = KPipeline(lang_code=lang, repo_id=REPO, device=device)
        return self.pipelines[key]

    # ---- api exposed to JS
    def state(self):
        s = self._settings()
        return {
            "voices": [dict(id=i, name=n, accent=a, gender=g) for i, n, a, g in VOICES],
            "theme": s.get("theme"),
            "voice": s.get("voice", "af_heart"),
            "speed": s.get("speed", 1.0),
            "device": s.get("device", "auto"),
            "mps": mps_available(),
            "history": self.history,
        }

    def set_pref(self, key, value):
        if key in ("theme", "voice", "speed", "device"):
            self._set(**{key: value})

    def reveal(self, path):
        subprocess.run(["open", "-R", path])

    def cancel(self):
        self.cancel_flag.set()

    def generate(self, text, voice, speed):
        with self.lock:
            if self.busy:
                return {"error": "Already generating"}
            self.busy = True
        self.cancel_flag.clear()
        threading.Thread(target=self._work, args=(text, voice, float(speed)), daemon=True).start()
        return {"started": True}

    def _work(self, text, voice, speed):
        try:
            device = self._device()
            if (voice[0], device) not in self.pipelines:
                msg = "Loading model…" if model_cached() else "Downloading model (~330 MB, first run only)…"
                self._js("onStatus", {"msg": msg, "pct": None})
            try:
                chunks, words = self._synth(text, voice, speed, device)
            except Exception as e:
                if isinstance(e, Cancelled) or device != "mps":
                    raise
                chunks, words = self._synth(text, voice, speed, "cpu")  # GPU path failed: retry on CPU
            if not chunks:
                raise RuntimeError("No audio produced")
            audio = np.concatenate(chunks)
            id_ = uuid.uuid4().hex[:12]
            sf.write(self._audio_path(id_), audio, SAMPLE_RATE)
            entry = {
                "id": id_,
                "text": " ".join(text.split())[:80],
                "slug": re.sub(r"[^a-z0-9]+", "-", text.lower())[:30].strip("-") or "speech",
                "voice": VOICE_NAMES.get(voice, voice),
                "duration": len(audio) / SAMPLE_RATE,
                "ts": int(time.time()),
                "saved": None,
                "fulltext": text,
                "words": words,
            }
            self.history.insert(0, entry)
            self._save_history()
            self._js("onDone", entry)
        except Cancelled:
            self._js("onCancelled", {})
        except Exception as e:
            self._js("onError", {"msg": str(e)})
        finally:
            self.busy = False

    def _synth(self, text, voice, speed, device):
        """Returns (audio chunks, word timings). Each timing is [start_s, end_s, char_from, char_to]."""
        pipe = self._pipeline(voice[0], device)
        total = max(len(text), 1)
        done = 0
        chunks = []
        words = []
        cursor = 0
        offset = 0.0
        self._js("onStatus", {"msg": "Generating…", "pct": 0})
        for res in pipe(text, voice=voice, speed=speed, split_pattern=r"\n+"):
            if self.cancel_flag.is_set():
                raise Cancelled()
            if res.audio is None:
                continue
            audio = res.audio.detach().cpu().numpy().astype(np.float32)
            dur = len(audio) / SAMPLE_RATE
            found, cursor = align_chunk(text, cursor, res, offset, dur)
            words.extend(found)
            offset += dur
            chunks.append(audio)
            done += len(res.graphemes or "")
            self._js("onStatus", {"msg": "Generating…", "pct": min(done / total, 1.0)})
        return chunks, words

    def get_audio(self, id_):
        with open(self._audio_path(id_), "rb") as f:
            return "data:audio/wav;base64," + base64.b64encode(f.read()).decode()

    def save(self, id_):
        entry = next((h for h in self.history if h["id"] == id_), None)
        if not entry:
            return {"error": "Not found"}
        folder = self._settings().get("folder") or os.path.expanduser("~/Desktop")
        r = self.window.create_file_dialog(
            webview.FileDialog.SAVE, directory=folder, save_filename=f"{entry['slug']}.wav",
        )
        if not r:
            return {"cancelled": True}
        path = r if isinstance(r, str) else r[0]
        if os.path.splitext(path)[1].lower() not in (".wav", ".mp3", ".flac", ".ogg"):
            path += ".wav"
        try:
            data, sr = sf.read(self._audio_path(id_))
            sf.write(path, data, sr)
        except Exception as e:
            return {"error": str(e)}
        entry["saved"] = path
        self._set(folder=os.path.dirname(path))
        write_json(HISTORY, self.history)
        return {"path": path}

    def delete(self, id_):
        self.history = [h for h in self.history if h["id"] != id_]
        try:
            os.remove(self._audio_path(id_))
        except OSError:
            pass
        write_json(HISTORY, self.history)


def set_app_name(name):
    try:  # macOS menu bar / Dock show the bundle name, which would be "Python"
        from Foundation import NSBundle
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = name
    except Exception:
        pass


if __name__ == "__main__":
    set_app_name("Kokoro Studio")
    api = Api()
    api.window = webview.create_window(
        "Kokoro Studio", os.path.join(HERE, "ui.html"), js_api=api,
        width=1040, height=760, min_size=(760, 560),
        background_color="#ffffff", text_select=True,
    )
    webview.start()
