import struct
from pathlib import Path
from core.bytes_util import TesBytes


class BinaryFileReader:
    """バイナリファイルリーダー基底クラス"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._data = self._path.read_bytes()
        self._pos = 0

    @property
    def position(self) -> int:
        return self._pos

    @property
    def length(self) -> int:
        return len(self._data)

    @property
    def eof(self) -> bool:
        return self._pos >= self.length

    def seek(self, offset: int) -> None:
        self._pos = offset

    def skip(self, count: int) -> None:
        self._pos += count

    def read_bytes(self, count: int) -> TesBytes:
        data = self._data[self._pos: self._pos + count]
        self._pos += count
        return TesBytes(data)

    def peek_bytes(self, count: int, offset: int = 0) -> TesBytes:
        start = self._pos + offset
        return TesBytes(self._data[start: start + count])

    def read_str(self, count: int) -> str:
        return self.read_bytes(count).raw().decode("ascii", errors="replace")

    def read_uint32(self) -> int:
        val, = struct.unpack_from("<I", self._data, self._pos)
        self._pos += 4
        return val

    def read_uint16(self) -> int:
        val, = struct.unpack_from("<H", self._data, self._pos)
        self._pos += 2
        return val

    def peek_str(self, count: int, offset: int = 0) -> str:
        return self.peek_bytes(count, offset).raw().decode("ascii", errors="replace")
