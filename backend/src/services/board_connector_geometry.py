"""Stored connector geometry; anchor formulas mirror frontend board/anchors.ts."""

from copy import deepcopy
from math import floor, hypot, inf, isclose
from typing import Any

from src.schemas.board_shapes import MAX_LINE_POINTS
from src.services.board_layout import DiagramLayoutError

EPSILON = 1e-9


def _same(a: float, b: float) -> bool:
    return isclose(a, b, abs_tol=EPSILON, rel_tol=0)


def anchor_point(
    node: dict[str, Any], anchor: str, toward: list[float] | None = None
) -> list[float]:
    x, y, w, h = (node[key] for key in ("x", "y", "w", "h"))
    cx, cy = x + w / 2, y + h / 2
    cardinal = {
        "top": [cx, y],
        "bottom": [cx, y + h],
        "left": [x, cy],
        "right": [x + w, cy],
    }
    if anchor != "center":
        return cardinal[anchor]
    if toward is None:
        return [cx, cy]
    dx, dy = toward[0] - cx, toward[1] - cy
    hw, hh = w / 2, h / 2
    if (dx == 0 and dy == 0) or hw <= 0 or hh <= 0:
        return [cx, cy]
    kind = node.get("kind") if node.get("type") == "shape" else "sticker"
    if kind == "ellipse":
        scale = 1 / hypot(dx / hw, dy / hh)
    elif kind == "diamond":
        scale = 1 / (abs(dx) / hw + abs(dy) / hh)
    else:
        scale = min(hw / abs(dx) if dx else inf, hh / abs(dy) if dy else inf)
    scale = min(scale, 1)
    return [cx + dx * scale, cy + dy * scale]


def _round_coordinate(value: float) -> float:
    scaled = value * 10
    lower = floor(scaled)
    return (lower + (scaled - lower >= 0.5)) / 10


def connector_endpoints(arrow: dict, current: dict, boxes: dict) -> tuple[list[float], list[float]]:
    """Match connectorPoints: rough anchor first, centre clipped toward opposite rough anchor."""
    nodes = {**current, **boxes}
    refs = [arrow.get("start_ref"), arrow.get("end_ref")]
    old = [arrow["points"][0], arrow["points"][-1]]
    keys = [(ref["type"], ref["id"]) if ref else None for ref in refs]
    targets = [nodes.get(key) for key in keys]
    rough = [
        anchor_point(node, ref.get("anchor", "center")) if node and ref else old[i]
        for i, (node, ref) in enumerate(zip(targets, refs, strict=True))
    ]
    result = deepcopy(old)
    touches_selected = any(key in boxes for key in keys)
    for i, (node, ref, key) in enumerate(zip(targets, refs, keys, strict=True)):
        # A centre contour contact depends on the opposite rough anchor even
        # when its own node stays put. Cardinal/free external ends stay fixed.
        if (
            node
            and ref
            and (key in boxes or (touches_selected and ref.get("anchor", "center") == "center"))
        ):
            point = anchor_point(node, ref.get("anchor", "center"), rough[1 - i])
            # JavaScript Math.round, including negative half values.
            result[i] = [_round_coordinate(value) for value in point]
    return result[0], result[1]


def _axis(a: list[float], b: list[float]) -> str:
    x_equal, y_equal = _same(a[0], b[0]), _same(a[1], b[1])
    if x_equal and y_equal:
        return "zero"
    if x_equal:
        return "vertical"
    if y_equal:
        return "horizontal"
    return "diagonal"


def adjust_connector_points(
    stored: list[list[float]], start: list[float], end: list[float], route: str
) -> list[list[float]]:
    """Keep the manual route, propagating endpoint constraints through entire axis runs."""
    if len(stored) > MAX_LINE_POINTS:
        raise DiagramLayoutError("В маршруте стрелки должно быть не больше 50 точек.")
    points = deepcopy(stored)
    ds = [start[i] - stored[0][i] for i in range(2)]
    de = [end[i] - stored[-1][i] for i in range(2)]
    if all(_same(ds[i], de[i]) for i in range(2)):
        translated = [[x + ds[0], y + ds[1]] for x, y in stored]
        translated[0], translated[-1] = list(start), list(end)
        return translated
    points[0], points[-1] = list(start), list(end)
    if route != "orthogonal":
        return points
    axes = [_axis(a, b) for a, b in zip(stored, stored[1:], strict=False)]
    if "diagonal" in axes:
        # Preserve user-defined diagonal paths rather than silently rerouting them.
        return points
    meaningful = set(axes) - {"zero"}
    if not meaningful and (_same(start[0], end[0]) or _same(start[1], end[1])):
        return [list(start), list(end)]
    if len(meaningful) <= 1:
        # A wholly collinear run cannot meet two conflicting perpendicular endpoint
        # constraints. It has no real bends to preserve; use at most four elbows.
        horizontal = meaningful != {"vertical"}
        fixed = 1 if horizontal else 0
        if not _same(start[fixed], end[fixed]):
            along = 1 - fixed
            lane = (start[along] + end[along]) / 2
            first, last = list(start), list(end)
            first[along] = last[along] = lane
            return [list(start), first, last, list(end)]
    # Each horizontal component fixes y; each vertical component fixes x.
    # Zero-length segments join both runs. Old axes, not mutated points, drive
    # propagation, so a subdivided first/last segment stays fully orthogonal.
    for dimension, axis in ((0, "vertical"), (1, "horizontal")):
        begin = 0
        while begin < len(stored):
            finish = begin
            while finish < len(axes) and axes[finish] in {axis, "zero"}:
                finish += 1
            value = (
                start[dimension]
                if begin == 0
                else (end[dimension] if finish == len(stored) - 1 else stored[begin][dimension])
            )
            for index in range(begin, finish + 1):
                points[index][dimension] = value
            begin = finish + 1
    return points
