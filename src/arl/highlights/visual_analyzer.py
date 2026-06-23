"""视觉分析器：为condensed模式计算视觉活跃度。

采样视频帧并计算：
- scene_change_rate: 场景变化率（相邻帧差异）
- minimap_activity: 小地图活跃度（右下角区域变化）
- edge_density_variance: 边缘密度变化（画面复杂度）
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from arl.shared.logging import log


def analyze_visual_activity(
    video_path: Path,
    sample_interval_seconds: float = 10.0,
    weight_scene_change: float = 0.5,
    weight_minimap: float = 0.3,
    weight_edge_density: float = 0.2,
    minimap_region: tuple[float, float, float, float] = (0.75, 0.75, 1.0, 1.0),
) -> float:
    """分析视频的视觉活跃度。

    Args:
        video_path: 视频文件路径
        sample_interval_seconds: 采样间隔（秒）
        weight_scene_change: 场景变化率权重
        weight_minimap: 小地图活跃度权重
        weight_edge_density: 边缘密度权重
        minimap_region: 小地图区域 (x1, y1, x2, y2)，归一化坐标 [0, 1]

    Returns:
        视觉活跃度评分 [0, 1]，失败时返回 0.0
    """
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            log(
                "highlights",
                f"visual_analyzer: failed to open video {video_path}, "
                "degrading to visual_activity=0.0",
            )
            return 0.0

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            log(
                "highlights",
                f"visual_analyzer: invalid fps={fps} for {video_path}, "
                "degrading to visual_activity=0.0",
            )
            cap.release()
            return 0.0

        frame_interval = int(fps * sample_interval_seconds)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        frames = _sample_frames_for_condensed(cap, frame_interval, total_frames)
        cap.release()

        if len(frames) < 2:
            log(
                "highlights",
                f"visual_analyzer: insufficient frames ({len(frames)}) "
                f"sampled from {video_path}, degrading to visual_activity=0.0",
            )
            return 0.0

        scene_change = _compute_scene_change_rate(frames)
        minimap_act = _compute_minimap_activity(frames, minimap_region)
        edge_var = _compute_edge_density_variance(frames)

        visual_activity = (
            weight_scene_change * scene_change
            + weight_minimap * minimap_act
            + weight_edge_density * edge_var
        )

        log(
            "highlights",
            f"visual_analyzer: {video_path.name} "
            f"scene_change={scene_change:.3f} "
            f"minimap={minimap_act:.3f} "
            f"edge_var={edge_var:.3f} "
            f"→ visual_activity={visual_activity:.3f}"
        )

        return float(np.clip(visual_activity, 0.0, 1.0))

    except Exception as e:
        log(
            "highlights",
            f"visual_analyzer: exception analyzing {video_path}: {e}, "
            "degrading to visual_activity=0.0",
        )
        return 0.0


def _sample_frames_for_condensed(
    cap: cv2.VideoCapture, frame_interval: int, total_frames: int
) -> list[np.ndarray]:
    """按间隔采样视频帧。

    Args:
        cap: opencv VideoCapture对象
        frame_interval: 采样间隔（帧数）
        total_frames: 视频总帧数

    Returns:
        采样帧列表（灰度图）
    """
    frames = []
    frame_positions = range(0, total_frames, frame_interval)

    for pos in frame_positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        if width > 320:
            target_height = max(1, int(height * (320 / width)))
            gray = cv2.resize(gray, (320, target_height), interpolation=cv2.INTER_AREA)
        frames.append(gray)

    return frames

def _compute_scene_change_rate(frames: list[np.ndarray]) -> float:
    """计算场景变化率：相邻帧之间的平均差异。

    Args:
        frames: 灰度帧列表

    Returns:
        场景变化率 [0, 1]
    """
    if len(frames) < 2:
        return 0.0

    diffs = []
    for i in range(1, len(frames)):
        # 计算帧间绝对差异
        diff = cv2.absdiff(frames[i - 1], frames[i])
        # 归一化到 [0, 1]
        mean_diff = diff.mean() / 255.0
        diffs.append(mean_diff)

    return float(np.mean(diffs))


def _compute_minimap_activity(
    frames: list[np.ndarray], region: tuple[float, float, float, float]
) -> float:
    """计算小地图区域活跃度。

    Args:
        frames: 灰度帧列表
        region: 小地图区域 (x1, y1, x2, y2)，归一化坐标

    Returns:
        小地图活跃度 [0, 1]
    """
    if len(frames) < 2:
        return 0.0

    x1, y1, x2, y2 = region
    h, w = frames[0].shape

    # 转换为像素坐标
    x1_px = int(x1 * w)
    y1_px = int(y1 * h)
    x2_px = int(x2 * w)
    y2_px = int(y2 * h)

    minimap_diffs = []
    for i in range(1, len(frames)):
        roi_prev = frames[i - 1][y1_px:y2_px, x1_px:x2_px]
        roi_curr = frames[i][y1_px:y2_px, x1_px:x2_px]

        if roi_prev.size == 0 or roi_curr.size == 0:
            continue

        diff = cv2.absdiff(roi_prev, roi_curr)
        mean_diff = diff.mean() / 255.0
        minimap_diffs.append(mean_diff)

    return float(np.mean(minimap_diffs)) if minimap_diffs else 0.0


def _compute_edge_density_variance(frames: list[np.ndarray]) -> float:
    """计算边缘密度变化：画面复杂度随时间的方差。

    Args:
        frames: 灰度帧列表

    Returns:
        边缘密度变化 [0, 1]
    """
    if len(frames) < 2:
        return 0.0

    edge_densities = []
    for frame in frames:
        # Canny边缘检测
        edges = cv2.Canny(frame, threshold1=50, threshold2=150)
        # 计算边缘像素占比
        density = edges.sum() / (255.0 * edges.size)
        edge_densities.append(density)

    # 计算方差并归一化
    variance = float(np.var(edge_densities))
    # 经验归一化：方差通常 < 0.01，映射到 [0, 1]
    normalized_variance = min(variance * 100.0, 1.0)

    return normalized_variance
