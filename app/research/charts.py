"""ECharts option builders. Colors are injected client-side from the active theme's --chart palette."""
from __future__ import annotations

from typing import Any

TILE = {  # state tile-grid (col, row)
    "AK": (0, 0), "ME": (11, 0), "VT": (10, 1), "NH": (11, 1),
    "WA": (1, 2), "ID": (2, 2), "MT": (3, 2), "ND": (4, 2), "MN": (5, 2), "IL": (6, 2), "WI": (7, 2), "MI": (8, 2),
    "NY": (9, 2), "RI": (10, 2), "MA": (11, 2),
    "OR": (1, 3), "NV": (2, 3), "WY": (3, 3), "SD": (4, 3), "IA": (5, 3), "IN": (6, 3), "OH": (7, 3), "PA": (8, 3),
    "NJ": (9, 3), "CT": (10, 3),
    "CA": (1, 4), "UT": (2, 4), "CO": (3, 4), "NE": (4, 4), "MO": (5, 4), "KY": (6, 4), "WV": (7, 4), "VA": (8, 4),
    "MD": (9, 4), "DE": (10, 4),
    "AZ": (2, 5), "NM": (3, 5), "KS": (4, 5), "AR": (5, 5), "TN": (6, 5), "NC": (7, 5), "SC": (8, 5), "DC": (9, 5),
    "OK": (4, 6), "LA": (5, 6), "MS": (6, 6), "AL": (7, 6), "GA": (8, 6),
    "HI": (0, 7), "TX": (4, 7), "FL": (9, 7),
}


def fmt_usd(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.{digits}f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.{digits}f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.{digits}f}K"
    return f"${v:,.0f}"


def fmt_int(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f}"


def fmt_pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}%"


def _base(**kw: Any) -> dict[str, Any]:
    opt: dict[str, Any] = {"tooltip": {"trigger": "axis", "confine": True}, "grid": {"left": 8, "right": 16, "top": 32,
                           "bottom": 8, "containLabel": True}, "legend": {"top": 0, "type": "scroll"}}
    opt.update(kw)
    return opt


def bar(categories: list[str], series: list[tuple[str, list[float | None]]], *, horizontal: bool = False,
        stacked: bool = False, unit: str = "") -> dict[str, Any]:
    cat_axis = {"type": "category", "data": categories, "axisLabel": {"interval": 0, "width": 140, "overflow": "truncate"}}
    val_axis = {"type": "value", "name": unit, "nameTextStyle": {"align": "left"}}
    ser = [{"name": n, "type": "bar", "data": v, "barMaxWidth": 28, **({"stack": "s"} if stacked else {}),
            "emphasis": {"focus": "series"}} for n, v in series]
    return _base(xAxis=val_axis if horizontal else cat_axis, yAxis=cat_axis if horizontal else val_axis, series=ser,
                 legend={"top": 0, "show": len(series) > 1})


def line(xs: list[str], series: list[tuple[str, list[float | None]]], unit: str = "") -> dict[str, Any]:
    ser = [{"name": n, "type": "line", "data": v, "smooth": True, "symbolSize": 6, "connectNulls": True}
           for n, v in series]
    return _base(xAxis={"type": "category", "data": xs, "boundaryGap": False},
                 yAxis={"type": "value", "name": unit}, series=ser)


def heatmap(xcats: list[str], ycats: list[str], cells: list[list[float]], vmax: float = 100.0,
            unit: str = "%") -> dict[str, Any]:
    return {"tooltip": {"position": "top"}, "grid": {"left": 8, "right": 8, "top": 8, "bottom": 56, "containLabel": True},
            "xAxis": {"type": "category", "data": xcats, "splitArea": {"show": True}},
            "yAxis": {"type": "category", "data": ycats, "splitArea": {"show": True}},
            "visualMap": {"min": 0, "max": vmax, "calculable": True, "orient": "horizontal", "left": "center",
                          "bottom": 0, "text": [f"{vmax:.0f}{unit}", f"0{unit}"], "ll_ramp": "seq"},
            "series": [{"type": "heatmap", "data": cells, "label": {"show": True, "formatter": "ll:pct0"}}]}


def state_tiles(values: dict[str, float | None], label: str, vmin: float | None = None, vmax: float | None = None,
                diverging: bool = False) -> dict[str, Any]:
    data, vals = [], [v for v in values.values() if v is not None]
    for st, (c, r) in TILE.items():
        v = values.get(st)
        data.append({"value": [c, r, v if v is not None else "-"], "name": st})
    lo = vmin if vmin is not None else (min(vals) if vals else 0)
    hi = vmax if vmax is not None else (max(vals) if vals else 1)
    return {"tooltip": {"formatter": "ll:tile"}, "grid": {"left": 0, "right": 0, "top": 0, "bottom": 48},
            "xAxis": {"type": "category", "data": list(range(12)), "show": False},
            "yAxis": {"type": "category", "data": list(range(8)), "show": False, "inverse": True},
            "visualMap": {"min": lo, "max": hi, "calculable": True, "orient": "horizontal", "left": "center",
                          "bottom": 0, "text": [label, ""], "ll_ramp": "div" if diverging else "seq"},
            "series": [{"type": "heatmap", "data": data, "itemStyle": {"borderColor": "ll:bg", "borderWidth": 3,
                                                                       "borderRadius": 4},
                        "label": {"show": True, "formatter": "ll:tilelabel", "fontSize": 11}}]}


def quadrant(points: list[dict[str, Any]], xname: str, yname: str, xmid: float, ymid: float,
             labels: tuple[str, str, str, str]) -> dict[str, Any]:
    """labels = (top-right, top-left, bottom-left, bottom-right)."""
    return {"tooltip": {"formatter": "ll:point"}, "grid": {"left": 8, "right": 24, "top": 24, "bottom": 8,
                                                         "containLabel": True},
            "xAxis": {"type": "value", "name": xname, "nameLocation": "middle", "nameGap": 28, "scale": True},
            "yAxis": {"type": "value", "name": yname, "nameLocation": "middle", "nameGap": 44, "scale": True},
            "series": [{"type": "scatter", "symbolSize": 14, "data": [[p["x"], p["y"], p["name"]] for p in points],
                        "label": {"show": True, "formatter": "ll:name", "position": "right", "fontSize": 10},
                        "markLine": {"silent": True, "symbol": "none", "lineStyle": {"type": "dashed"},
                                     "data": [{"xAxis": xmid}, {"yAxis": ymid}], "label": {"show": False}}}],
            "graphic": [{"type": "text", "right": 30, "top": 28, "style": {"text": labels[0], "ll_text": "accent"}},
                        {"type": "text", "left": 70, "top": 28, "style": {"text": labels[1], "ll_text": "muted"}},
                        {"type": "text", "left": 70, "bottom": 40, "style": {"text": labels[2], "ll_text": "muted"}},
                        {"type": "text", "right": 30, "bottom": 40, "style": {"text": labels[3], "ll_text": "muted"}}]}


def treemap(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"tooltip": {"formatter": "ll:treemap"},
            "series": [{"type": "treemap", "roam": False, "nodeClick": False, "breadcrumb": {"show": False},
                        "data": items, "label": {"show": True, "formatter": "{b}"}, "itemStyle": {"gapWidth": 2,
                                                                                               "borderRadius": 6}}]}


def waterfall(steps: list[tuple[str, float]]) -> dict[str, Any]:
    base, vals, run = [], [], 0.0
    for _, v in steps:
        base.append(round(run if v >= 0 else run + v, 3))
        vals.append(round(abs(v), 3))
        run += v
    return _base(xAxis={"type": "category", "data": [s for s, _ in steps], "axisLabel": {"interval": 0, "rotate": 20}},
                 yAxis={"type": "value"}, legend={"show": False},
                 series=[{"type": "bar", "stack": "w", "data": base, "itemStyle": {"color": "transparent"},
                          "tooltip": {"show": False}},
                         {"type": "bar", "stack": "w", "data": vals, "label": {"show": True, "position": "top",
                                                                                "formatter": "ll:num1"}}])


def timeline_bars(rows: list[tuple[str, float, float, str]], title_x: str = "Year") -> dict[str, Any]:
    """rows: (label, start_year_decimal, end_year_decimal, group)."""
    groups = sorted({g for *_, g in rows})
    labels = [r[0] for r in rows]
    base = [round(r[1], 2) for r in rows]
    ser = [{"type": "bar", "stack": "t", "data": base, "itemStyle": {"color": "transparent"}, "tooltip": {"show": False},
            "name": "_base"}]
    for g in groups:
        ser.append({"type": "bar", "stack": "t", "name": g, "barMaxWidth": 14,
                    "data": [round(r[2] - r[1], 2) if r[3] == g else 0 for r in rows]})
    lo = min(base) if base else 2020
    return {"tooltip": {"trigger": "item", "formatter": "ll:span"}, "legend": {"top": 0, "data": groups},
            "grid": {"left": 8, "right": 16, "top": 32, "bottom": 8, "containLabel": True},
            "xAxis": {"type": "value", "min": int(lo), "minInterval": 1, "name": title_x,
                      "axisLabel": {"formatter": "ll:year"}},
            "yAxis": {"type": "category", "data": labels, "inverse": True,
                      "axisLabel": {"width": 220, "overflow": "truncate"}}, "series": ser}


def policy_radar(points: list[dict[str, Any]]) -> dict[str, Any]:
    sectors = ["MFN", "Tariffs", "PBM", "IRA"]
    rings = ["In effect", "Final", "Proposed", "Notice"]
    data = [[p["ring"], p["sector"], p["title"]] for p in points]
    return {"tooltip": {"formatter": "ll:radar"}, "polar": {"radius": ["12%", "80%"]},
            "angleAxis": {"type": "category", "data": sectors, "boundaryGap": True, "splitLine": {"show": True}},
            "radiusAxis": {"type": "category", "data": rings, "axisLabel": {"fontSize": 10}},
            "series": [{"type": "scatter", "coordinateSystem": "polar", "symbolSize": 12, "data": data,
                        "itemStyle": {"opacity": 0.8}}]}


def table(columns: list[str], rows: list[list[Any]], numeric: list[int] | None = None) -> dict[str, Any]:
    return {"columns": columns, "rows": rows, "numeric": numeric or []}
