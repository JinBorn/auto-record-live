from __future__ import annotations

import cv2
import numpy as np

from .models import FrameScene, SceneReading


def classify_scene(frame: np.ndarray, timestamp_seconds: float) -> SceneReading:
    """Classify coarse LoL scene state from stable HUD/loading-screen regions."""
    map_region = _relative_region(frame, 0.84, 0.64, 1.0, 1.0)
    hud_region = _relative_region(frame, 0.30, 0.78, 0.75, 1.0)
    top_region = _relative_region(frame, 0.78, 0.0, 1.0, 0.07)
    center_region = _relative_region(frame, 0.25, 0.18, 0.75, 0.65)

    map_edges = _edge_density(map_region)
    hud_edges = _edge_density(hud_region)
    top_dark = _dark_ratio(top_region)
    center_edges = _edge_density(center_region)

    if map_edges >= 0.10 and hud_edges >= 0.09:
        return SceneReading(
            timestamp_seconds=timestamp_seconds,
            scene="in_game",
            confidence=min(0.99, 0.55 + map_edges + hud_edges),
        )

    if top_dark >= 0.85 and center_edges >= 0.065 and map_edges < 0.08:
        # Extra guard: during real loading screens the bottom HUD (ability
        # bar, items) is completely absent. Death/respawn overlays can
        # otherwise match the loading profile because the screen dims, a
        # death-recap graphic adds center edges, and the minimap area is
        # sparse — but the ability bar is still there (grayed out with
        # cooldown digits). Requiring hud_edges < 0.05 rules out death
        # screens and avoids false match splits mid-game.
        if hud_edges >= 0.05:
            return SceneReading(
                timestamp_seconds=timestamp_seconds,
                scene="other",
                confidence=0.65,
            )
        return SceneReading(
            timestamp_seconds=timestamp_seconds,
            scene="loading",
            confidence=min(0.95, 0.45 + center_edges + top_dark * 0.4),
        )

    return SceneReading(
        timestamp_seconds=timestamp_seconds,
        scene="other",
        confidence=0.7,
    )


def looks_like_death_screen(frame: np.ndarray) -> bool:
    """Best-effort LoL death/respawn screen detector for edit-boundary guards."""
    hud_region = _relative_region(frame, 0.30, 0.78, 0.75, 1.0)
    top_region = _relative_region(frame, 0.78, 0.0, 1.0, 0.07)
    center_region = _relative_region(frame, 0.10, 0.10, 0.82, 0.72)

    return (
        _dark_ratio(top_region) >= 0.88
        and _edge_density(hud_region) >= 0.08
        and _mean_saturation(center_region) <= 0.24
    )


def _relative_region(
    frame: np.ndarray,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> np.ndarray:
    height, width = frame.shape[:2]
    left = max(0, min(width, int(width * x1)))
    right = max(left + 1, min(width, int(width * x2)))
    top = max(0, min(height, int(height * y1)))
    bottom = max(top + 1, min(height, int(height * y2)))
    return frame[top:bottom, left:right]


def _edge_density(region: np.ndarray) -> float:
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return float(np.count_nonzero(edges) / edges.size)


def _dark_ratio(region: np.ndarray) -> float:
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return float(np.count_nonzero(gray < 40) / gray.size)


def _mean_saturation(region: np.ndarray) -> float:
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 1]) / 255.0)
