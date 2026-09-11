"""Traditional Chinese display labels for internal status codes."""
from __future__ import annotations


_LABELS = {
    "pending": "待處理",
    "processing": "處理中",
    "running": "執行中",
    "paused": "已暫停",
    "completed": "已完成",
    "failed": "失敗",
    "skipped": "已略過",
    "cancelled": "已取消",
    "process": "影像處理",
    "import": "匯入",
    "export": "匯出",
    "automatic_only": "僅自動判定",
    "partially_verified": "部分已複核",
    "verified": "已確認",
    "plate_exact": "車牌完全相符",
    "plate_fuzzy": "車牌近似",
    "manual": "人工處理",
    "reid": "Re-ID",
    "mixed": "混合來源",
    "human_verified": "人工確認",
    "high_confidence_plate_match": "高信心車牌相符",
    "automatic_candidate": "自動候選",
}


def display_value(value: object) -> str:
    """Return a localized label while preserving unknown technical values."""
    text = str(value)
    return _LABELS.get(text, text)
