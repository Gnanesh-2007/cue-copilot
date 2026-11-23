"""
True Word-by-Word Streaming STT via Deepgram Nova-2 WebSocket (~150ms latency).
Streams 16 kHz mono PCM continuously over wss://api.deepgram.com/v1/listen:
  - Interim results -> real-time word-by-word live captions as the speaker talks
  - speech_final / UtteranceEnd -> fires the exact millisecond a sentence ends (0ms post-pause STT wait)
  - Automatic KeepAlive pings & reconnect; app.py seamlessly falls back to local CUDA Whisper if offline.
"""
import asyncio
import json
import os
import threading
import time
import numpy as np

DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-2&language=en&encoding=linear16&sample_rate=16000&channels=1"
    "&interim_results=true&smart_format=true&punctuate=true"
    "&endpointing=500&utterance_end_ms=1000"
)


def load_deepgram_key(settings_path: str) -> str:
    """Checks DEEPGRAM_API_KEY env var first, then settings.json ('deepgram_key')."""
    env_key = (os.environ.get("DEEPGRAM_API_KEY") or "").strip()
    if env_key:
        return env_key
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            return (json.load(f).get("deepgram_key") or "").strip()
    except Exception:
        return ""


class DeepgramStreamer:
    def __init__(self, api_key: str, on_interim, on_final, on_status=None):
        self.api_key = api_key.strip()
        self.on_interim = on_interim
        self.on_final = on_final
        self.on_status = on_status
        self.connected = False
        self._running = False
        self._loop = None
        self._queue = None
        self._thread = None

    def start(self):
        if not self.api_key or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self.connected = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def feed_float32(self, samples_16k: np.ndarray):
        """Non-blocking push of 16 kHz mono float32 audio into the WebSocket send queue."""
        if not self.connected or self._loop is None or self._queue is None or len(samples_16k) == 0:
            return
        pcm = np.clip(samples_16k * 32767.0, -32768, 32767).astype(np.int16).tobytes()

        def _enqueue():
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except Exception:
                    pass
            try:
                self._queue.put_nowait(pcm)
            except Exception:
                pass

        try:
            self._loop.call_soon_threadsafe(_enqueue)
        except Exception:
            pass

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue(maxsize=120)
        try:
            self._loop.run_until_complete(self._session_loop())
        except Exception as e:
            print(f"[Deepgram] Loop exited: {e}")
        finally:
            self.connected = False

    async def _session_loop(self):
        import websockets

        headers = {"Authorization": f"Token {self.api_key}"}
        backoff = 1.0

        while self._running:
            try:
                async with websockets.connect(
                    DEEPGRAM_URL,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**20,
                ) as ws:
                    self.connected = True
                    backoff = 1.0
                    print("[Deepgram] Live WebSocket connected (Nova-2, ~150ms streaming)")
                    if self.on_status:
                        self.on_status(True)

                    sender = asyncio.create_task(self._sender(ws))
                    receiver = asyncio.create_task(self._receiver(ws))
                    done, pending = await asyncio.wait(
                        [sender, receiver], return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
            except Exception as e:
                print(f"[Deepgram] Connection error ({e}); falling back to local Whisper until reconnect")
            finally:
                if self.connected and self.on_status:
                    self.on_status(False)
                self.connected = False

            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    async def _sender(self, ws):
        last_sent = time.time()
        while self._running:
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await ws.send(chunk)
                last_sent = time.time()
            except asyncio.TimeoutError:
                # Keep WebSocket alive during long silences so reconnect never stalls
                if time.time() - last_sent >= 7.0:
                    await ws.send(json.dumps({"type": "KeepAlive"}))
                    last_sent = time.time()

    async def _receiver(self, ws):
        final_parts = []
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
            except Exception:
                continue

            mtype = msg.get("type")
            if mtype == "Results":
                alts = msg.get("channel", {}).get("alternatives") or []
                transcript = (alts[0].get("transcript") or "").strip() if alts else ""
                is_final = bool(msg.get("is_final"))
                speech_final = bool(msg.get("speech_final"))

                if is_final:
                    if transcript:
                        final_parts.append(transcript)
                        self.on_interim(" ".join(final_parts))
                    if speech_final and final_parts:
                        full = " ".join(final_parts).strip()
                        final_parts.clear()
                        self.on_final(full)
                elif transcript:
                    preview = " ".join(final_parts + [transcript]).strip()
                    self.on_interim(preview)

            elif mtype == "UtteranceEnd":
                if final_parts:
                    full = " ".join(final_parts).strip()
                    final_parts.clear()
                    self.on_final(full)
