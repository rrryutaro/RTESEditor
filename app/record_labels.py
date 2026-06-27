from __future__ import annotations


_RECORD_TYPE_LABELS: dict[str, str] = {
    "TES3": "ヘッダー",
    "GMST": "ゲーム設定",
    "GLOB": "グローバル変数",
    "CLAS": "クラス",
    "FACT": "派閥",
    "RACE": "種族",
    "SOUN": "サウンド",
    "SKIL": "スキル",
    "MGEF": "魔法効果",
    "SCPT": "スクリプト",
    "REGN": "リージョン",
    "BSGN": "星座",
    "LTEX": "地表テクスチャ",
    "STAT": "静的オブジェクト",
    "DOOR": "ドア",
    "MISC": "その他アイテム",
    "WEAP": "武器",
    "CONT": "コンテナ",
    "SPEL": "呪文",
    "CREA": "クリーチャー",
    "BODY": "ボディパーツ",
    "LIGH": "ライト",
    "ENCH": "エンチャント",
    "NPC_": "NPC",
    "ARMO": "防具",
    "CLOT": "衣服",
    "REPA": "修理道具",
    "ACTI": "アクティベーター",
    "APPA": "錬金術器具",
    "LOCK": "開錠道具",
    "PROB": "探査道具",
    "INGR": "錬金素材",
    "BOOK": "本",
    "ALCH": "ポーション",
    "LEVI": "レベルリスト（アイテム）",
    "LEVC": "レベルリスト（クリーチャー）",
    "CELL": "セル",
    "LAND": "地形",
    "PGRD": "経路グリッド",
    "SNDG": "サウンド生成器",
    "DIAL": "ダイアログトピック",
    "INFO": "ダイアログ応答",
    "SSCR": "開始スクリプト",
}


def record_type_label(record_type: str, fallback: str | None = None, *, include_code: bool = False) -> str:
    key = (record_type or "").strip()
    label = _RECORD_TYPE_LABELS.get(key)
    if label:
        return f"{label} ({key})" if include_code else label
    if fallback and fallback != key:
        return f"{fallback} ({key})" if include_code and key else fallback
    return key
