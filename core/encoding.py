from enum import Enum


class TesEncoding(Enum):
    CP1252    = "cp1252"    # Morrowind オリジナル（英語）
    SHIFT_JIS = "shift_jis" # Morrowind 日本語版
    UTF_8     = "utf-8"     # OpenMW / 日本語化MOD

    @staticmethod
    def default() -> "TesEncoding":
        return TesEncoding.CP1252

    def label(self) -> str:
        labels = {
            TesEncoding.CP1252:    "CP1252（オリジナル英語版）",
            TesEncoding.SHIFT_JIS: "Shift-JIS（日本語版）",
            TesEncoding.UTF_8:     "UTF-8（OpenMW / 日本語化MOD）",
        }
        return labels[self]

    def short_label(self) -> str:
        return {
            TesEncoding.CP1252:    "CP1252",
            TesEncoding.SHIFT_JIS: "Shift-JIS",
            TesEncoding.UTF_8:     "UTF-8",
        }[self]

    @staticmethod
    def detect_from_bytes(data: bytes) -> "TesEncoding":
        """TES3 ファイルのフィールドデータを解析してエンコーディングを推定する"""
        if len(data) < 16 or data[:4] != b"TES3":
            return TesEncoding.CP1252

        # TES3 構造を解析してフィールドデータを収集
        field_samples: list[bytes] = []
        pos = 0
        while pos + 16 <= len(data) and len(field_samples) < 80:
            rec_size = int.from_bytes(data[pos + 4:pos + 8], "little")
            pos += 16
            rec_end = min(pos + rec_size, len(data))
            while pos + 8 <= rec_end:
                fld_size = int.from_bytes(data[pos + 4:pos + 8], "little")
                pos += 8
                fld_end = min(pos + fld_size, rec_end)
                field_samples.append(data[pos:fld_end])
                pos = fld_end
            pos = rec_end

        # UTF-8 として厳密にデコードできるフィールドがあれば UTF-8 と判定
        for fd in field_samples:
            stripped = fd.rstrip(b"\x00")
            if not stripped or not any(b > 0x7F for b in stripped):
                continue
            try:
                decoded = stripped.decode("utf-8")
                if any(ord(c) > 0x7F for c in decoded):
                    return TesEncoding.UTF_8
            except UnicodeDecodeError:
                pass

        # Shift-JIS リードバイト (0x81-0x9F, 0xE0-0xFC) の出現数で判定
        sjis_hits = 0
        for fd in field_samples:
            for b in fd[:128]:
                if 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC:
                    sjis_hits += 1
                    if sjis_hits >= 5:
                        return TesEncoding.SHIFT_JIS

        return TesEncoding.CP1252
