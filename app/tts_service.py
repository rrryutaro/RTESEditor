from __future__ import annotations

import io
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import traceback
import wave
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Callable


_SVSF_ASYNC = 1
_SVSF_PURGE_BEFORE_SPEAK = 2
_URL_RE = re.compile(r"https?://\S+")
_CREATE_NO_WINDOW = 0x08000000
_VOICEVOX_STARTUP_BUFFER_SEGMENTS = 2
_VOICEVOX_PREFETCH_QUEUE_SIZE = 4
_VOICEVOX_TRAILING_SILENT_CHARS = frozenset(
    "」』”’\"')）〕］】｝〉》〙〗〟〞>＞"
    "、，,・:：;；…‥ー―-"
    "!?！？"
)
_SegmentCallback = Callable[[int, str], None]
_FinishedCallback = Callable[[bool], None]


@dataclass(frozen=True)
class _VoicevoxSynthesisResult:
    index: int
    segment: str
    data: bytes | None
    error: str | None
    speaker: int
    chars: int


@dataclass(frozen=True)
class _TTSRequest:
    text: str
    force: bool
    generation: int
    segments: tuple[str, ...] | None = None
    on_segment_start: _SegmentCallback | None = None
    on_segment_end: _SegmentCallback | None = None
    on_finished: _FinishedCallback | None = None


def _log_tts_error(message: str) -> None:
    try:
        root = Path(__file__).resolve().parent.parent
        log_dir = root / "dist" / "RTESEditor"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "rteseditor_tts.log").open("a", encoding="utf-8") as fp:
            fp.write(message.rstrip() + "\n")
    except Exception:  # noqa: BLE001
        pass


def _text_id(text: str) -> str:
    value = str(text or "")
    digest = sha1(value.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{len(value)}:{digest}"


class TTSService:
    """SAPI5 / VOICEVOX による軽量読み上げサービス。

    RTESArenaAssist の読み上げサービスと同じく、UI スレッドを止めないように
    キュー + デーモンスレッドで発声する。pywin32 がない環境では何もしない。
    """

    def __init__(self, *, start_worker: bool = True) -> None:
        self._enabled = False
        self._interrupt = True
        self._rate = 0
        self._volume = 100
        self._voice_desc = ""
        self._engine = "sapi5"
        self._vv_speaker = 0
        self._generation = 0
        self._lock = threading.RLock()
        self._pause_cond = threading.Condition(self._lock)
        self._paused = False
        self._playback_process: subprocess.Popen | None = None
        self._voicevox_cache: OrderedDict[tuple[str, int, int, int], bytes] = OrderedDict()
        self._stopping = False
        self._queue: queue.Queue[_TTSRequest | tuple[str, bool] | tuple[str, bool, int] | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def set_enabled(self, value: bool) -> None:
        with self._lock:
            self._enabled = bool(value)

    def set_interrupt(self, value: bool) -> None:
        with self._lock:
            self._interrupt = bool(value)

    def set_rate(self, value: int) -> None:
        with self._lock:
            self._rate = max(-10, min(10, int(value)))

    def set_volume(self, value: int) -> None:
        with self._lock:
            self._volume = max(0, min(100, int(value)))

    def set_voice(self, desc: str) -> None:
        with self._lock:
            self._voice_desc = desc or ""

    def set_engine(self, value: str) -> None:
        with self._lock:
            self._engine = "voicevox" if str(value) == "voicevox" else "sapi5"

    def set_vv_speaker(self, value: int) -> None:
        try:
            speaker = int(value)
        except (TypeError, ValueError):
            speaker = 0
        with self._lock:
            self._vv_speaker = speaker

    def speak(self, text: str) -> None:
        if self._enabled:
            self._enqueue(text, force=False)

    def speak_now(self, text: str) -> None:
        self._enqueue(text, force=True)

    def speak_segments_now(
        self,
        segments: list[str] | tuple[str, ...],
        *,
        on_segment_start: _SegmentCallback | None = None,
        on_segment_end: _SegmentCallback | None = None,
        on_finished: _FinishedCallback | None = None,
    ) -> None:
        self._enqueue(
            "",
            force=True,
            segments=segments,
            on_segment_start=on_segment_start,
            on_segment_end=on_segment_end,
            on_finished=on_finished,
        )

    def pause_speaking(self) -> None:
        with self._pause_cond:
            self._paused = True
            self._pause_cond.notify_all()

    def resume_speaking(self) -> None:
        with self._pause_cond:
            self._paused = False
            self._pause_cond.notify_all()

    def stop_speaking(self) -> None:
        with self._pause_cond:
            self._generation += 1
            self._paused = False
            self._pause_cond.notify_all()
        self._drain()
        self._stop_playback(wait=False)
        self._queue.put(_TTSRequest("", True, self._generation))

    def shutdown(self) -> None:
        with self._pause_cond:
            self._stopping = True
            self._paused = False
            self._pause_cond.notify_all()
        self._drain()
        self._stop_playback(wait=False)
        self._queue.put(None)
        worker = self._worker
        if worker is not None and worker.is_alive() and threading.current_thread() is not worker:
            worker.join(timeout=3.0)
            if worker.is_alive():
                _log_tts_error("TTS worker did not stop within timeout.")

    def _enqueue(
        self,
        text: str,
        *,
        force: bool,
        segments: list[str] | tuple[str, ...] | None = None,
        on_segment_start: _SegmentCallback | None = None,
        on_segment_end: _SegmentCallback | None = None,
        on_finished: _FinishedCallback | None = None,
    ) -> None:
        segment_tuple: tuple[str, ...] | None = None
        if segments is not None:
            segment_tuple = tuple(self._sanitize(segment) if segment else "" for segment in segments)
            value = "\n\n".join(segment for segment in segment_tuple if segment)
        else:
            value = self._sanitize(text)
        if not value:
            return
        with self._pause_cond:
            if self._stopping:
                return
            interrupt = self._interrupt
            engine = self._engine
            if interrupt:
                self._generation += 1
                self._paused = False
                self._pause_cond.notify_all()
            generation = self._generation
        if interrupt:
            self._drain()
            if engine == "voicevox":
                self._stop_playback(wait=False)
        _log_tts_error(
            "TTS enqueue: "
            f"id={_text_id(value)} force={force} interrupt={interrupt} engine={engine} generation={generation}"
        )
        self._queue.put(
            _TTSRequest(
                value,
                force,
                generation,
                segment_tuple,
                on_segment_start,
                on_segment_end,
                on_finished,
            )
        )

    def _stop_playback(self, *, wait: bool) -> None:
        process: subprocess.Popen | None = None
        with self._lock:
            if self._playback_process is not None and self._playback_process.poll() is None:
                process = self._playback_process
            self._playback_process = None
        if process is not None:
            try:
                process.terminate()
                if wait:
                    process.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                try:
                    process.kill()
                    if wait:
                        process.wait(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:  # noqa: BLE001
            pass

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    @staticmethod
    def _sanitize(text: str) -> str:
        value = _URL_RE.sub("", str(text or ""))
        for ch in ("'", "’", "‘", "`"):
            value = value.replace(ch, "")
        return value.strip()

    def _run(self) -> None:
        speaker = None
        had_sapi = False
        while True:
            entry = self._queue.get()
            if entry is None:
                break
            if isinstance(entry, _TTSRequest):
                request = entry
            else:
                if len(entry) == 2:
                    text, force = entry
                    with self._lock:
                        generation = self._generation
                else:
                    text, force, generation = entry
                request = _TTSRequest(text, force, generation)
            try:
                with self._lock:
                    enabled = self._enabled
                    engine = self._engine
                if not request.force and not enabled:
                    continue
                if engine == "voicevox":
                    if speaker is not None:
                        speaker = None
                        if had_sapi:
                            self._uninitialize_com()
                            had_sapi = False
                    ok = self._speak_voicevox(
                        request.text,
                        request.generation,
                        segments=request.segments,
                        on_segment_start=request.on_segment_start,
                        on_segment_end=request.on_segment_end,
                    )
                    self._notify_finished(request.on_finished, ok)
                    continue
                if speaker is None:
                    speaker = self._init_sapi5()
                    had_sapi = speaker is not None
                if speaker is not None:
                    if request.segments is not None:
                        ok = self._speak_sapi5_segments(
                            speaker,
                            request.segments,
                            request.generation,
                            request.on_segment_start,
                            request.on_segment_end,
                        )
                        self._notify_finished(request.on_finished, ok)
                    else:
                        self._speak_sapi5(speaker, request.text)
                        self._notify_finished(request.on_finished, True)
            except Exception:  # noqa: BLE001
                _log_tts_error("TTS worker error:\n" + traceback.format_exc())
                if isinstance(entry, _TTSRequest):
                    self._notify_finished(entry.on_finished, False)
                continue
        speaker = None
        if had_sapi:
            self._uninitialize_com()

    def _speak_sapi5(self, speaker, text: str) -> None:
        try:
            with self._lock:
                volume = self._volume
                rate = self._rate
                interrupt = self._interrupt
            speaker.Volume = volume
            speaker.Rate = rate
            self._apply_voice(speaker)
            if interrupt or not text:
                speaker.Speak(text, _SVSF_ASYNC | _SVSF_PURGE_BEFORE_SPEAK)
            else:
                speaker.Speak(text)
        except Exception:  # noqa: BLE001
            _log_tts_error("SAPI5 speak error:\n" + traceback.format_exc())
            pass

    def _speak_voicevox(
        self,
        text: str,
        generation: int,
        *,
        segments: tuple[str, ...] | None = None,
        on_segment_start: _SegmentCallback | None = None,
        on_segment_end: _SegmentCallback | None = None,
    ) -> bool:
        value = (text or "").strip()
        if not value and not segments:
            return True
        ok = False
        segment_list = list(segments) if segments is not None else self._split_voicevox_text(value)
        index = self._next_voicevox_segment_index(segment_list, 0)
        if index < 0:
            return True
        _log_tts_error(
            "VOICEVOX speak start: "
            f"id={_text_id(value)} generation={generation} segments={len([s for s in segment_list if s])}"
        )

        result_queue = self._start_voicevox_prefetch(segment_list, index, generation)
        prefetched: dict[int, _VoicevoxSynthesisResult] = {}
        startup_buffered = False
        while index >= 0:
            if not self._is_generation_current(generation):
                return ok
            if not self._wait_if_paused(generation):
                return ok

            result = self._get_voicevox_prefetch_result(
                result_queue,
                prefetched,
                index,
                generation,
                consume=True,
            )
            if result is None:
                return ok
            if result.error:
                _log_tts_error(
                    f"VOICEVOX synthesize error: speaker={result.speaker} chars={result.chars}\n{result.error}"
                )
                return ok
            if not result.data:
                _log_tts_error(
                    f"VOICEVOX synthesize returned no audio: speaker={result.speaker} chars={result.chars}"
                )
                return ok

            if not startup_buffered:
                self._buffer_voicevox_startup_results(
                    result_queue,
                    prefetched,
                    segment_list,
                    index,
                    generation,
                )
                startup_buffered = True
                if not self._is_generation_current(generation):
                    return ok
                if not self._wait_if_paused(generation):
                    return ok

            next_index = self._next_voicevox_segment_index(segment_list, index + 1)
            try:
                _log_tts_error(
                    "VOICEVOX play: "
                    f"generation={generation} segment={index + 1}/{len(segment_list)} "
                    f"chars={result.chars} bytes={len(result.data)}"
                )
                self._notify_segment(on_segment_start, index, segment_list[index])
                self._play_wav(result.data, generation)
                if not self._is_generation_current(generation):
                    return ok
                self._notify_segment(on_segment_end, index, segment_list[index])
                ok = True
            except Exception:  # noqa: BLE001
                _log_tts_error("VOICEVOX playback error:\n" + traceback.format_exc())
                return ok

            gap_end = next_index if next_index >= 0 else len(segment_list)
            for gap in segment_list[index + 1:gap_end]:
                if not self._is_generation_current(generation):
                    return ok
                if not self._wait_if_paused(generation):
                    return ok
                if not gap:
                    time.sleep(0.25)
            index = next_index
        return ok

    def _speak_sapi5_segments(
        self,
        speaker,
        segments: tuple[str, ...],
        generation: int,
        on_segment_start: _SegmentCallback | None,
        on_segment_end: _SegmentCallback | None,
    ) -> bool:
        ok = False
        for index, segment in enumerate(segments):
            if not self._is_generation_current(generation):
                return ok
            if not self._wait_if_paused(generation):
                return ok
            if not segment:
                time.sleep(0.25)
                continue
            self._notify_segment(on_segment_start, index, segment)
            self._speak_sapi5_blocking(speaker, segment)
            if not self._is_generation_current(generation):
                return ok
            self._notify_segment(on_segment_end, index, segment)
            ok = True
        return ok

    def _speak_sapi5_blocking(self, speaker, text: str) -> None:
        try:
            with self._lock:
                volume = self._volume
                rate = self._rate
            speaker.Volume = volume
            speaker.Rate = rate
            self._apply_voice(speaker)
            speaker.Speak(text)
        except Exception:  # noqa: BLE001
            _log_tts_error("SAPI5 segmented speak error:\n" + traceback.format_exc())

    @staticmethod
    def _notify_segment(callback: _SegmentCallback | None, index: int, text: str) -> None:
        if callback is None:
            return
        try:
            callback(index, text)
        except Exception:  # noqa: BLE001
            _log_tts_error("TTS segment callback error:\n" + traceback.format_exc())

    @staticmethod
    def _notify_finished(callback: _FinishedCallback | None, ok: bool) -> None:
        if callback is None:
            return
        try:
            callback(bool(ok))
        except Exception:  # noqa: BLE001
            _log_tts_error("TTS finished callback error:\n" + traceback.format_exc())

    def _start_voicevox_prefetch(
        self,
        segments: list[str],
        start_index: int,
        generation: int,
    ) -> queue.Queue[_VoicevoxSynthesisResult]:
        result_queue: queue.Queue[_VoicevoxSynthesisResult] = queue.Queue(
            maxsize=_VOICEVOX_PREFETCH_QUEUE_SIZE
        )
        segment_list = list(segments)

        def worker() -> None:
            index = start_index
            while index >= 0 and self._is_generation_current(generation):
                segment = segment_list[index]
                speaker = -1
                chars = len(segment)
                try:
                    started = time.perf_counter()
                    data, speaker = self._synthesize_voicevox_segment(segment)
                    elapsed = time.perf_counter() - started
                    if elapsed >= 4.0:
                        _log_tts_error(
                            f"VOICEVOX synthesize slow: speaker={speaker} chars={chars} seconds={elapsed:.2f}"
                        )
                    result = _VoicevoxSynthesisResult(index, segment, data, None, speaker, chars)
                except Exception:  # noqa: BLE001
                    result = _VoicevoxSynthesisResult(
                        index,
                        segment,
                        None,
                        traceback.format_exc(),
                        speaker,
                        chars,
                    )
                if not self._put_voicevox_prefetch_result(result_queue, result, generation):
                    return
                if result.error or not result.data:
                    return
                index = self._next_voicevox_segment_index(segment_list, index + 1)

        threading.Thread(target=worker, daemon=True, name="RTESEditorVoicevoxPrefetch").start()
        return result_queue

    def _put_voicevox_prefetch_result(
        self,
        result_queue: queue.Queue[_VoicevoxSynthesisResult],
        result: _VoicevoxSynthesisResult,
        generation: int,
    ) -> bool:
        while self._is_generation_current(generation):
            try:
                result_queue.put(result, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _get_voicevox_prefetch_result(
        self,
        result_queue: queue.Queue[_VoicevoxSynthesisResult],
        prefetched: dict[int, _VoicevoxSynthesisResult],
        index: int,
        generation: int,
        *,
        consume: bool,
    ) -> _VoicevoxSynthesisResult | None:
        result = prefetched.get(index)
        if result is not None:
            if consume:
                prefetched.pop(index, None)
            return result
        while self._is_generation_current(generation):
            try:
                result = result_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            prefetched[result.index] = result
            if result.index == index:
                if consume:
                    prefetched.pop(index, None)
                return result
        return None

    def _buffer_voicevox_startup_results(
        self,
        result_queue: queue.Queue[_VoicevoxSynthesisResult],
        prefetched: dict[int, _VoicevoxSynthesisResult],
        segments: list[str],
        current_index: int,
        generation: int,
    ) -> None:
        buffered = 1
        next_index = self._next_voicevox_segment_index(segments, current_index + 1)
        while buffered < _VOICEVOX_STARTUP_BUFFER_SEGMENTS and next_index >= 0:
            result = self._get_voicevox_prefetch_result(
                result_queue,
                prefetched,
                next_index,
                generation,
                consume=False,
            )
            if result is None:
                return
            buffered += 1
            if result.error or not result.data:
                return
            next_index = self._next_voicevox_segment_index(segments, next_index + 1)

    def _synthesize_voicevox_segment(self, segment: str) -> tuple[bytes | None, int]:
        from app import voicevox_client

        with self._lock:
            rate = self._rate
            volume_value = self._volume
            speaker = self._vv_speaker
            cache_key = (segment, speaker, rate, volume_value)
            cached = self._voicevox_cache.get(cache_key)
            if cached is not None:
                self._voicevox_cache.move_to_end(cache_key)
                return cached, speaker
        speed = max(0.5, min(2.0, 1.0 + rate / 20.0))
        volume = max(0.0, min(2.0, volume_value / 100.0))
        data = voicevox_client.synthesize(
            segment,
            speaker,
            speed=speed,
            volume=volume,
            timeout=8.0,
        )
        if data:
            with self._lock:
                self._voicevox_cache[cache_key] = data
                self._voicevox_cache.move_to_end(cache_key)
                while len(self._voicevox_cache) > 48:
                    self._voicevox_cache.popitem(last=False)
        return data, speaker

    def _is_generation_current(self, generation: int) -> bool:
        with self._lock:
            return not self._stopping and generation == self._generation

    def _wait_if_paused(self, generation: int) -> bool:
        with self._pause_cond:
            while self._paused and not self._stopping and generation == self._generation:
                self._pause_cond.wait(timeout=0.05)
            return not self._stopping and generation == self._generation

    def _is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @staticmethod
    def _split_voicevox_text(text: str) -> list[str]:
        segments: list[str] = []
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        for paragraph_index, paragraph in enumerate(paragraphs):
            sentences = TTSService._split_voicevox_paragraph(paragraph)
            segments.extend(sentences or [re.sub(r"\s*\n\s*", " ", paragraph).strip()])
            if paragraph_index < len(paragraphs) - 1:
                segments.append("")
        return segments

    @staticmethod
    def _split_voicevox_paragraph(paragraph: str) -> list[str]:
        value = str(paragraph or "").replace("\r\n", "\n").replace("\r", "\n")
        sentences: list[str] = []
        start = 0
        index = 0
        while index < len(value):
            if value[index] != "。":
                index += 1
                continue
            end = TTSService._voicevox_sentence_end_index(value, index + 1)
            sentence = re.sub(r"\s*\n\s*", " ", value[start:end]).strip()
            if sentence:
                sentences.append(sentence)
            start = end
            index = end
        tail = re.sub(r"\s*\n\s*", " ", value[start:]).strip()
        if tail:
            sentences.append(tail)
        return sentences

    @staticmethod
    def _voicevox_sentence_end_index(text: str, start: int) -> int:
        index = start
        while index < len(text):
            ch = text[index]
            if ch in "\r\n":
                break
            if ch.isspace() or ch in _VOICEVOX_TRAILING_SILENT_CHARS:
                index += 1
                continue
            break
        return index

    @staticmethod
    def _next_voicevox_segment_index(segments: list[str], start: int) -> int:
        for index in range(start, len(segments)):
            if segments[index]:
                return index
        return -1

    def _play_wav(self, data: bytes, generation: int | None = None) -> None:
        if os.name == "nt":
            import winsound

            path = ""
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fp:
                    fp.write(data)
                    path = fp.name
                duration = self._wav_duration_seconds(data)
                while True:
                    if generation is not None and not self._wait_if_paused(generation):
                        return
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    deadline = time.perf_counter() + max(0.1, duration + 0.1)
                    restart = False
                    while time.perf_counter() < deadline:
                        if generation is not None and not self._is_generation_current(generation):
                            winsound.PlaySound(None, winsound.SND_PURGE)
                            return
                        if self._is_paused():
                            winsound.PlaySound(None, winsound.SND_PURGE)
                            restart = True
                            break
                        time.sleep(0.03)
                    if not restart:
                        break
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            return

        path = ""
        process: subprocess.Popen | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fp:
                fp.write(data)
                path = fp.name
            escaped_path = path.replace("'", "''")
            command = (
                "$ErrorActionPreference = 'Stop'; "
                f"$p = '{escaped_path}'; "
                "$player = New-Object System.Media.SoundPlayer $p; "
                "$player.Load(); "
                "$player.PlaySync()"
            )
            process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            with self._lock:
                self._playback_process = process
            return_code = process.wait()
            if return_code:
                _log_tts_error(f"VOICEVOX playback process exited: {return_code}")
        finally:
            with self._lock:
                if process is not None and self._playback_process is process:
                    self._playback_process = None
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    @staticmethod
    def _wav_duration_seconds(data: bytes) -> float:
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception:  # noqa: BLE001
            pass
        return max(0.3, min(30.0, len(data) / 88200.0))

    def _apply_voice(self, speaker) -> None:
        with self._lock:
            voice_desc = self._voice_desc
        if not voice_desc:
            return
        try:
            voices = speaker.GetVoices()
            for i in range(voices.Count):
                token = voices.Item(i)
                if token.GetDescription() == voice_desc:
                    speaker.Voice = token
                    return
        except Exception:  # noqa: BLE001
            _log_tts_error("SAPI5 apply voice error:\n" + traceback.format_exc())
            pass

    @staticmethod
    def _init_sapi5():
        initialized = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            initialized = True
            import win32com.client.dynamic
            return win32com.client.dynamic.Dispatch("SAPI.SpVoice")
        except Exception:  # noqa: BLE001
            if initialized:
                try:
                    pythoncom.CoUninitialize()
                except Exception:  # noqa: BLE001
                    pass
            _log_tts_error("SAPI5 init error:\n" + traceback.format_exc())
            return None

    @staticmethod
    def _uninitialize_com() -> None:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def list_voices(timeout: float = 1.5) -> list[str]:
        """利用可能な SAPI5 音声の説明一覧を返す。

        SAPI の COM 列挙が環境依存で戻らないことがあるため、
        UI スレッドを塞がないようタイムアウト付きのデーモンスレッドで実行する。
        """
        out: list[str] = []
        error: list[str] = []

        def worker() -> None:
            try:
                out.extend(TTSService._list_voices_blocking())
            except Exception:  # noqa: BLE001
                error.append(traceback.format_exc())

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=max(0.1, timeout))
        if thread.is_alive():
            _log_tts_error("SAPI5 list voices timed out.")
            return []
        if error:
            _log_tts_error("SAPI5 list voices error:\n" + error[0])
        return out

    @staticmethod
    def _list_voices_blocking() -> list[str]:
        initialized = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            initialized = True
            import win32com.client.dynamic
            speaker = win32com.client.dynamic.Dispatch("SAPI.SpVoice")
            voices = speaker.GetVoices()
            out: list[str] = []
            for i in range(voices.Count):
                try:
                    out.append(voices.Item(i).GetDescription())
                except Exception:  # noqa: BLE001
                    pass
            return out
        except Exception:  # noqa: BLE001
            _log_tts_error("SAPI5 list voices error:\n" + traceback.format_exc())
            return []
        finally:
            if initialized:
                try:
                    pythoncom.CoUninitialize()
                except Exception:  # noqa: BLE001
                    pass


__all__ = ["TTSService"]
