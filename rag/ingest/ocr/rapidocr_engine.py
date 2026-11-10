"""RapidOCR (onnxruntime) with pinned model files."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from rag.ingest.ocr import EngineLine, EngineWord

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
        return {"engine": "rapidocr", "version": version("rapidocr"), "models": dict(MODELS)}

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

    def recognize(self, image: np.ndarray) -> tuple[EngineLine, ...]:
        result = self._load()(image, return_word_box=True)
        if not result.txts:
            return ()
        lines = []
        for text, score, box, words in zip(result.txts, result.scores, result.boxes, result.word_results):
            parsed = tuple(EngineWord(str(word), _quad(quad), float(confidence))
                           for word, confidence, quad in words if quad is not None and str(word).strip())
            lines.append(EngineLine(str(text), _quad(box), float(score), parsed))
        return tuple(lines)
