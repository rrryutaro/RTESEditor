import struct
from core.encoding import TesEncoding


def _is_sjis_lead(b: int) -> bool:
    return 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC


def _sjis_decode_preprocess(data: bytes) -> bytes:
    """ESP保存時に Shift-JIS トレイルバイト 0x40 が 0x7F に置換されているものを元に戻す"""
    result = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if _is_sjis_lead(b) and i + 1 < len(data):
            trail = data[i + 1]
            result.append(b)
            result.append(0x40 if trail == 0x7F else trail)
            i += 2
        else:
            result.append(b)
            i += 1
    return bytes(result)


def _sjis_encode_postprocess(data: bytes) -> bytes:
    """Shift-JIS エンコード後、トレイルバイト 0x40 を 0x7F に置換する（ESP保存用）"""
    result = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if _is_sjis_lead(b) and i + 1 < len(data):
            trail = data[i + 1]
            result.append(b)
            result.append(0x7F if trail == 0x40 else trail)
            i += 2
        else:
            result.append(b)
            i += 1
    return bytes(result)


class TesBytes:
    """バイト列ラッパー。型変換・文字列変換を提供する。"""

    def __init__(self, data: bytes = b""):
        self._data = bytes(data)

    def __len__(self) -> int:
        return len(self._data)

    def raw(self) -> bytes:
        return self._data

    # --- 数値変換 ---

    def to_int8(self) -> int:
        return struct.unpack_from("<b", self._data)[0]

    def to_int16(self) -> int:
        return struct.unpack_from("<h", self._data)[0]

    def to_int32(self) -> int:
        return struct.unpack_from("<i", self._data)[0]

    def to_uint16(self) -> int:
        return struct.unpack_from("<H", self._data)[0]

    def to_uint32(self) -> int:
        return struct.unpack_from("<I", self._data)[0]

    def to_uint64(self) -> int:
        return struct.unpack_from("<Q", self._data)[0]

    def to_float32(self) -> float:
        return struct.unpack_from("<f", self._data)[0]

    # --- 文字列変換 ---

    def to_str(self, encoding: TesEncoding = TesEncoding.CP1252) -> str:
        raw = self._data.rstrip(b"\x00")
        if encoding == TesEncoding.SHIFT_JIS:
            raw = _sjis_decode_preprocess(raw)
        return raw.decode(encoding.value, errors="replace")

    # --- ファクトリ ---

    @staticmethod
    def from_str(value: str, encoding: TesEncoding, null_terminate: bool = False) -> "TesBytes":
        data = value.encode(encoding.value)
        if encoding == TesEncoding.SHIFT_JIS:
            data = _sjis_encode_postprocess(data)
        if null_terminate:
            data += b"\x00"
        return TesBytes(data)

    @staticmethod
    def from_uint32(value: int) -> "TesBytes":
        return TesBytes(struct.pack("<I", value))
