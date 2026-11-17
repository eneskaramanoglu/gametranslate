"""Yedek OCR: RapidOCR (PaddleOCR modelleri, ONNX Runtime). ``uv sync --extra rapid``."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .base import OcrLine, OcrUnavailable


class RapidOcr:
    name = "rapid"

    def __init__(self, use_dml: bool = False) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OcrUnavailable(
                "RapidOCR kurulu değil. Kurmak için: uv sync --extra rapid\n"
                "(GPU için ayrıca: uv pip install onnxruntime-directml ve [ocr] rapid_use_dml = true)"
            ) from exc
        kwargs = {}
        if use_dml:
            kwargs = dict(det_use_dml=True, cls_use_dml=True, rec_use_dml=True)
        self._engine = RapidOCR(**kwargs)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rapidocr")

    def _run(self, gray: np.ndarray) -> list[OcrLine]:
        result, _elapsed = self._engine(gray, use_cls=False)
        lines: list[OcrLine] = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(
                OcrLine(
                    text=str(text),
                    x=int(min(xs)),
                    y=int(min(ys)),
                    w=int(max(xs) - min(xs)),
                    h=int(max(ys) - min(ys)),
                    confidence=float(score),
                )
            )
        return lines

    async def recognize(self, gray: np.ndarray) -> list[OcrLine]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._run, gray)
