from __future__ import annotations

import math
from typing import Optional

import cv2
import numpy as np


def _deskew_angle_degrees(gray: np.ndarray) -> float:
    """
    Estimate skew angle from binarized text lines.
    Returns degrees in [-45, 45].
    """
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    thr = 255 - thr

    coords = np.column_stack(np.where(thr > 0))
    if coords.size == 0:
        return 0.0
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle
    return float(angle)


def _rotate(image: np.ndarray, angle_degrees: float) -> np.ndarray:
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle_degrees, 1.0)
    cos = abs(m[0, 0])
    sin = abs(m[0, 1])

    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))
    m[0, 2] += (new_w / 2) - center[0]
    m[1, 2] += (new_h / 2) - center[1]

    return cv2.warpAffine(image, m, (new_w, new_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess_for_ocr(
    bgr: np.ndarray,
    *,
    deskew: bool = True,
    denoise: bool = True,
    target_width: Optional[int] = None,
) -> np.ndarray:
    """
    OpenCV preprocessing intended to improve OCR quality.
    Returns a binarized (0/255) single-channel image.
    """
    if target_width and bgr.shape[1] < target_width:
        scale = target_width / bgr.shape[1]
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if denoise:
        gray = cv2.fastNlMeansDenoising(gray, h=15)

    if deskew:
        angle = _deskew_angle_degrees(gray)
        if not math.isfinite(angle):
            angle = 0.0
        if abs(angle) >= 0.3:
            bgr = _rotate(bgr, angle)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            if denoise:
                gray = cv2.fastNlMeansDenoising(gray, h=15)

    thr = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        11,
    )
    return thr

