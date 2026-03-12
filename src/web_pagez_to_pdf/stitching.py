"""Frame overlap detection and vertical stitching for scroll captures."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

DEFAULT_MIN_OVERLAP = 48
DEFAULT_MAX_OVERLAP = 520
DEFAULT_SCORE_THRESHOLD = 7.5


@dataclass(slots=True)
class StitchResult:
    """Result of stitching frames into one long image."""

    image: Image.Image
    overlaps: list[int]


def estimate_vertical_overlap(
    previous: Image.Image,
    current: Image.Image,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
    max_overlap: int = DEFAULT_MAX_OVERLAP,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> int:
    """Estimate overlap by minimizing mean absolute grayscale row difference."""

    prev_gray = np.asarray(previous.convert("L"), dtype=np.int16)
    curr_gray = np.asarray(current.convert("L"), dtype=np.int16)

    if prev_gray.shape[1] != curr_gray.shape[1]:
        width = min(prev_gray.shape[1], curr_gray.shape[1])
        prev_gray = prev_gray[:, :width]
        curr_gray = curr_gray[:, :width]

    max_candidate = min(max_overlap, prev_gray.shape[0] - 1, curr_gray.shape[0] - 1)
    if max_candidate < min_overlap:
        return 0

    best_overlap = 0
    best_score = float("inf")
    for overlap in range(max_candidate, min_overlap - 1, -8):
        prev_strip = prev_gray[-overlap:, :]
        curr_strip = curr_gray[:overlap, :]
        score = float(np.mean(np.abs(prev_strip - curr_strip)))
        if score < best_score:
            best_score = score
            best_overlap = overlap

    if best_score > score_threshold:
        return 0
    return best_overlap


def stitch_frames(frames: list[Image.Image]) -> StitchResult:
    """Stitch sequential frames vertically with overlap removal."""

    if not frames:
        raise ValueError("frames must not be empty")
    if len(frames) == 1:
        return StitchResult(image=frames[0].copy(), overlaps=[])

    overlaps: list[int] = []
    stitched = frames[0].copy()
    for frame in frames[1:]:
        overlap = estimate_vertical_overlap(stitched, frame)
        overlaps.append(overlap)
        crop_top = max(0, overlap)
        appended = frame.crop((0, crop_top, frame.width, frame.height))
        canvas = Image.new(
            "RGB",
            (max(stitched.width, appended.width), stitched.height + appended.height),
            "white",
        )
        canvas.paste(stitched, (0, 0))
        canvas.paste(appended, (0, stitched.height))
        stitched = canvas
    return StitchResult(image=stitched, overlaps=overlaps)


def frame_diff_score(a: Image.Image, b: Image.Image) -> float:
    """Return mean absolute difference score between two frames."""

    gray_a = np.asarray(a.convert("L"), dtype=np.int16)
    gray_b = np.asarray(b.convert("L"), dtype=np.int16)
    if gray_a.shape != gray_b.shape:
        width = min(gray_a.shape[1], gray_b.shape[1])
        height = min(gray_a.shape[0], gray_b.shape[0])
        gray_a = gray_a[:height, :width]
        gray_b = gray_b[:height, :width]
    return float(np.mean(np.abs(gray_a - gray_b)))
