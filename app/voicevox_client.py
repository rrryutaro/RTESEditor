from __future__ import annotations

import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 50021


def _base() -> str:
    return f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"


def is_available(timeout: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(_base() + "/version", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:  # noqa: BLE001
        return False


def list_speakers(timeout: float = 3.0) -> list[dict]:
    try:
        req = urllib.request.Request(_base() + "/speakers", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []

    out: list[dict] = []
    for speaker in raw:
        try:
            styles = [
                {"name": style.get("name", ""), "id": int(style.get("id"))}
                for style in speaker.get("styles", [])
                if style.get("id") is not None
            ]
            if styles:
                out.append({"name": speaker.get("name", ""), "styles": styles})
        except Exception:  # noqa: BLE001
            continue
    return out


def _read_json_response(resp):
    raw = resp.read().decode("utf-8")
    if not raw:
        return None
    return json.loads(raw)


def _voicevox_http_error(exc: HTTPError) -> RuntimeError:
    body = exc.read().decode("utf-8", errors="replace")[:500]
    return RuntimeError(f"VOICEVOX HTTP {exc.code}: {body}")


def get_user_dict_words(timeout: float = 3.0) -> list[dict]:
    """VOICEVOX のユーザー辞書を取得する。"""
    try:
        req = urllib.request.Request(_base() + "/user_dict", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = _read_json_response(resp) or {}
    except HTTPError as exc:
        raise _voicevox_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"VOICEVOX user_dict error: {exc}") from exc

    out: list[dict] = []
    if not isinstance(raw, dict):
        return out
    for word_uuid, word in raw.items():
        if not isinstance(word, dict):
            continue
        entry = dict(word)
        entry["voicevox_uuid"] = str(word_uuid)
        out.append(entry)
    out.sort(key=lambda item: (str(item.get("surface", "")), str(item.get("pronunciation", ""))))
    return out


def add_user_dict_word(
    surface: str,
    pronunciation: str,
    accent_type: int,
    *,
    word_type: str | None = None,
    priority: int | None = None,
    timeout: float = 5.0,
) -> str:
    query = _user_dict_word_query(surface, pronunciation, accent_type, word_type, priority)
    try:
        req = urllib.request.Request(
            _base() + "/user_dict_word?" + query,
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8").strip()
    except HTTPError as exc:
        raise _voicevox_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"VOICEVOX user_dict_word add error: {exc}") from exc

    if not raw:
        return ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return str(value or "")


def rewrite_user_dict_word(
    word_uuid: str,
    surface: str,
    pronunciation: str,
    accent_type: int,
    *,
    word_type: str | None = None,
    priority: int | None = None,
    timeout: float = 5.0,
) -> None:
    query = _user_dict_word_query(surface, pronunciation, accent_type, word_type, priority)
    try:
        req = urllib.request.Request(
            _base() + "/user_dict_word/" + urllib.parse.quote(str(word_uuid)) + "?" + query,
            data=b"",
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return
    except HTTPError as exc:
        raise _voicevox_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"VOICEVOX user_dict_word rewrite error: {exc}") from exc


def delete_user_dict_word(word_uuid: str, *, timeout: float = 5.0) -> None:
    try:
        req = urllib.request.Request(
            _base() + "/user_dict_word/" + urllib.parse.quote(str(word_uuid)),
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return
    except HTTPError as exc:
        raise _voicevox_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"VOICEVOX user_dict_word delete error: {exc}") from exc


def _user_dict_word_query(
    surface: str,
    pronunciation: str,
    accent_type: int,
    word_type: str | None,
    priority: int | None,
) -> str:
    params: dict[str, str | int] = {
        "surface": str(surface or ""),
        "pronunciation": str(pronunciation or ""),
        "accent_type": int(accent_type),
    }
    if word_type:
        params["word_type"] = str(word_type)
    if priority is not None:
        params["priority"] = max(0, min(10, int(priority)))
    return urllib.parse.urlencode(params)


def synthesize(
    text: str,
    speaker_id: int,
    *,
    speed: float = 1.0,
    volume: float = 1.0,
    timeout: float = 15.0,
) -> bytes | None:
    value = (text or "").strip()
    if not value:
        return None
    try:
        query_string = urllib.parse.urlencode({"text": value, "speaker": int(speaker_id)})
        req = urllib.request.Request(
            _base() + "/audio_query?" + query_string,
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            query = json.loads(resp.read().decode("utf-8"))

        query["speedScale"] = float(speed)
        query["volumeScale"] = float(volume)

        synthesis_query = urllib.parse.urlencode({"speaker": int(speaker_id)})
        body = json.dumps(query).encode("utf-8")
        req = urllib.request.Request(
            _base() + "/synthesis?" + synthesis_query,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"VOICEVOX HTTP {exc.code}: {body}") from exc


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "add_user_dict_word",
    "delete_user_dict_word",
    "get_user_dict_words",
    "is_available",
    "list_speakers",
    "rewrite_user_dict_word",
    "synthesize",
]
