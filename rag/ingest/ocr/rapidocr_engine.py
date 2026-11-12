"""RapidOCR (onnxruntime) with pinned model files.

Every image is read twice, with the line-orientation classifier on and off; both reads detect the same
boxes, and each box keeps the higher-confidence reading, the classifier-off one on a tie (decision 019).
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from rag.ingest.ocr import EngineLine, EngineWord

LINE_ORIENTATION = "cls_on_off_higher_confidence"
TEXT_SCORE = 0.5    # RapidOCR's default filter: lines below it are dropped by the engine

MODELS = {
    "det": "PP-OCRv6_det_small.onnx",
    "rec": "PP-OCRv6_rec_small.onnx",
    "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
}


def _quad(points: Any) -> tuple[tuple[float, float], ...]:
    return tuple((float(x), float(y)) for x, y in points)


class RapidOcrEngine:
    def __init__(self) -> None:
        self._engine: Any = None

    def identity(self) -> dict[str, Any]:
        return {"engine": "rapidocr", "version": version("rapidocr"), "models": dict(MODELS),
                "line_orientation": LINE_ORIENTATION}

    def _load(self) -> Any:
        if self._engine is None:
            import rapidocr
            from rapidocr import RapidOCR
            folder = Path(rapidocr.__file__).parent / "models"
            missing = [name for name in MODELS.values() if not (folder / name).is_file()]
            if missing:
                raise RuntimeError(f"RapidOCR 模型文件缺失：{missing}（目录 {folder}）")
            self._engine = RapidOCR(params={
                "Global.log_level": "error",
                "Det.model_path": str(folder / MODELS["det"]),
                "Rec.model_path": str(folder / MODELS["rec"]),
                "Cls.model_path": str(folder / MODELS["cls"]),
            })
        return self._engine

    def _read(self, image: np.ndarray, use_cls: bool) -> tuple[EngineLine, ...]:
        # RapidOCR keeps call arguments as engine state, so every call passes them all.
        result = self._load()(image, return_word_box=True, use_cls=use_cls, text_score=TEXT_SCORE)
        if not result.txts:
            return ()
        lines = []
        for text, score, box, words in zip(result.txts, result.scores, result.boxes, result.word_results):
            parsed = tuple(EngineWord(str(word), _quad(quad), float(confidence))
                           for word, confidence, quad in words if quad is not None and str(word).strip())
            lines.append(EngineLine(str(text), _quad(box), float(score), parsed))
        return tuple(lines)

    def recognize(self, image: np.ndarray) -> tuple[EngineLine, ...]:
        return higher_confidence(self._read(image, use_cls=True), self._read(image, use_cls=False))


def higher_confidence(cls_on: tuple[EngineLine, ...], cls_off: tuple[EngineLine, ...]) -> tuple[EngineLine, ...]:
    """Per detected box (same quad in both reads), the higher-confidence reading; the classifier-off one on a tie.

    Both reads keep the detection order and differ only in which boxes fell below the engine's filter, so
    they are merged in that order; a box kept by only one read takes that read.
    """
    on_quads = {line.quad for line in cls_on}
    off_quads = {line.quad for line in cls_off}
    chosen: list[EngineLine] = []
    i = j = 0
    while i < len(cls_on) or j < len(cls_off):
        a = cls_on[i] if i < len(cls_on) else None
        b = cls_off[j] if j < len(cls_off) else None
        if a is not None and b is not None and a.quad == b.quad:
            chosen.append(b if b.confidence >= a.confidence else a)
            i, j = i + 1, j + 1
        elif a is not None and a.quad not in off_quads:
            chosen.append(a)
            i += 1
        elif b is not None and b.quad not in on_quads:
            chosen.append(b)
            j += 1
        else:
            raise RuntimeError("RapidOCR 两遍识别的检测框顺序不一致")
    return tuple(chosen)
