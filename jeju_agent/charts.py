"""차트 생성기: 세션 표 → plotly 그림(JSON 저장).

디자인 원칙(데이터 시각화 가이드 준수):
- 범주 색은 고정 순서의 검증된 팔레트를 쓴다(색맹 안전). 9개 이상 계열은 '기타'로 묶는다.
- 크기(양)는 한 가지 색조의 순차 팔레트, 이중 축은 쓰지 않는다.
- 얇은 선(2px), 보조선은 옅게, 제목·축 이름은 한국어로 명확하게.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

from .config import CHART_DIR
from .session import AnalysisSession, ChartRecord

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
FONT_FAMILY = "Malgun Gothic, Apple SD Gothic Neo, NanumGothic, Noto Sans KR, Pretendard, sans-serif"
JEJU_CENTER = {"lat": 33.38, "lon": 126.55}
MAX_SERIES = 8
MAX_PIE_SLICES = 6

AGG_LABEL = {"sum": "합계", "mean": "평균", "median": "중앙값", "count": "건수", "max": "최대", "min": "최소"}


class ChartError(ValueError):
    pass


def _require_columns(df: pd.DataFrame, cols: list[str | None]) -> None:
    missing = [c for c in cols if c and c not in df.columns]
    if missing:
        raise ChartError(f"표에 없는 컬럼: {missing}. 사용 가능한 컬럼: {list(df.columns)}")


def _fold_series(df: pd.DataFrame, color: str, y: str | None) -> pd.DataFrame:
    """계열이 8개를 넘으면 값이 작은 계열을 '기타'로 묶는다(색 구분 한계)."""
    cats = df[color].astype(str)
    if cats.nunique() <= MAX_SERIES:
        return df
    if y and pd.api.types.is_numeric_dtype(df[y]):
        keep = df.groupby(cats)[y].sum().sort_values(ascending=False).head(MAX_SERIES - 1).index
    else:
        keep = cats.value_counts().head(MAX_SERIES - 1).index
    df = df.copy()
    df[color] = cats.where(cats.isin(keep), "기타")
    return df


def prepare_data(
    df: pd.DataFrame,
    chart_type: str,
    x: str,
    y: str | None,
    color: str | None,
    value: str | None,
    agg: str | None,
    top_n: int | None,
    sort: str,
    filter_expr: str | None,
) -> tuple[pd.DataFrame, str | None, list[str]]:
    notes: list[str] = []
    if filter_expr:
        try:
            df = df.query(filter_expr, engine="python")
        except Exception as e:  # noqa: BLE001
            raise ChartError(f"filter 식 오류: {e}") from e
        notes.append(f"필터 적용: {filter_expr} → {len(df):,}행")
    if df.empty:
        raise ChartError("표에 데이터가 없습니다(필터 결과 0행).")

    if chart_type in ("heatmap", "map"):
        return df, y, notes

    keys = [k for k in (x, color) if k]
    if agg:
        if agg == "count" and not y:
            df = df.groupby(keys, dropna=False).size().reset_index(name="건수")
            y = "건수"
        else:
            if not y:
                raise ChartError("agg 를 쓰려면 y 컬럼이 필요합니다.")
            if agg != "count":
                df = df.copy()
                df[y] = pd.to_numeric(df[y], errors="coerce")
            df = df.groupby(keys, dropna=False)[y].agg(agg).reset_index()
        notes.append(f"{'·'.join(keys)}별 {AGG_LABEL.get(agg, agg)} 집계")
    elif chart_type not in ("scatter", "histogram", "box") and y and len(df) > 5000:
        notes.append("집계 없이 5,000행 이상을 그리면 복잡해질 수 있습니다. agg 지정을 권장합니다.")

    if color and chart_type not in ("scatter", "histogram", "box"):
        df = _fold_series(df, color, y)

    if top_n and y and pd.api.types.is_numeric_dtype(df[y]):
        if color:
            totals = df.groupby(x)[y].sum().sort_values(ascending=False).head(top_n).index
            df = df[df[x].isin(totals)]
        else:
            df = df.sort_values(y, ascending=False).head(top_n)
        notes.append(f"상위 {top_n}개 표시")

    if sort in ("asc", "desc") and y and pd.api.types.is_numeric_dtype(df[y]):
        order = df.groupby(x)[y].sum().sort_values(ascending=(sort == "asc")).index.tolist()
        df = df.copy()
        df[x] = pd.Categorical(df[x], categories=order, ordered=True)
        df = df.sort_values(x)
    elif pd.api.types.is_datetime64_any_dtype(df[x]) or chart_type in ("line", "area"):
        df = df.sort_values(x)
    return df, y, notes


def build_figure(
    df: pd.DataFrame,
    chart_type: str,
    x: str,
    y: str | None,
    color: str | None,
    value: str | None,
    title: str,
    x_label: str | None,
    y_label: str | None,
    agg: str | None = None,
) -> go.Figure:
    labels = {}
    if x_label:
        labels[x] = x_label
    if y_label and y:
        labels[y] = y_label
    common = dict(color_discrete_sequence=PALETTE, template="plotly_white", labels=labels, title=title)

    if chart_type == "line":
        fig = px.line(df, x=x, y=y, color=color, markers=len(df) <= 400, **common)
        fig.update_traces(line={"width": 2})
        fig.update_layout(hovermode="x unified")
    elif chart_type == "area":
        fig = px.area(df, x=x, y=y, color=color, **common)
        fig.update_layout(hovermode="x unified")
    elif chart_type in ("bar", "stacked_bar"):
        fig = px.bar(df, x=x, y=y, color=color, barmode="stack" if chart_type == "stacked_bar" else "group", **common)
        fig.update_traces(marker_line_width=0)
    elif chart_type == "barh":
        fig = px.bar(df, x=y, y=x, color=color, orientation="h", barmode="group", **common)
        fig.update_layout(yaxis={"autorange": "reversed"})
    elif chart_type == "scatter":
        size = value if value and value in df.columns else None
        fig = px.scatter(df, x=x, y=y, color=color, size=size, opacity=0.8, **common)
        fig.update_traces(marker={"size": 9} if not size else {})
    elif chart_type == "pie":
        if not y:
            raise ChartError("pie 에는 값 컬럼(y)이 필요합니다.")
        agg_df = df.groupby(x, dropna=False)[y].sum().sort_values(ascending=False)
        if len(agg_df) > MAX_PIE_SLICES:
            top = agg_df.head(MAX_PIE_SLICES - 1)
            agg_df = pd.concat([top, pd.Series({"기타": agg_df.iloc[MAX_PIE_SLICES - 1 :].sum()})])
        fig = px.pie(agg_df.reset_index(), names=x, values=y, color_discrete_sequence=PALETTE, template="plotly_white", title=title)
        fig.update_traces(textposition="inside", textinfo="percent+label", sort=False)
    elif chart_type == "histogram":
        fig = px.histogram(df, x=x, color=color, nbins=30, **common)
        fig.update_layout(bargap=0.05)
    elif chart_type == "box":
        fig = px.box(df, x=x, y=y, color=color, **common)
    elif chart_type == "heatmap":
        if not y:
            raise ChartError("heatmap 에는 세로 범주 컬럼(y)이 필요합니다.")
        val = value if value else None
        if val:
            pivot = df.pivot_table(index=y, columns=x, values=val, aggfunc=agg or "sum")
        else:
            pivot = df.pivot_table(index=y, columns=x, aggfunc="size").fillna(0)
        fig = px.imshow(
            pivot,
            color_continuous_scale=SEQUENTIAL,
            aspect="auto",
            text_auto=".3s" if pivot.size <= 400 else False,
            template="plotly_white",
            title=title,
            labels={"color": (val or "건수") if not y_label else y_label},
        )
    elif chart_type == "map":
        if not y:
            raise ChartError("map 에는 위도 컬럼(y)과 경도 컬럼(x)이 필요합니다.")
        size = value if value and value in df.columns else None
        kwargs = dict(lat=y, lon=x, color=color, size=size, hover_name=color if color else None, zoom=9, center=JEJU_CENTER, color_discrete_sequence=PALETTE, title=title)
        if hasattr(px, "scatter_map"):
            fig = px.scatter_map(df, map_style="open-street-map", **kwargs)
        else:  # plotly < 5.24
            fig = px.scatter_mapbox(df, mapbox_style="open-street-map", **kwargs)
        fig.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0})
    else:
        raise ChartError(f"지원하지 않는 차트 유형: {chart_type}")

    fig.update_layout(
        font={"family": FONT_FAMILY, "size": 13},
        title={"x": 0.02, "xanchor": "left", "font": {"size": 17}},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0, "title_text": color or ""},
        margin={"l": 50, "r": 24, "t": 72, "b": 50},
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
    )
    if chart_type not in ("pie", "map", "heatmap"):
        fig.update_xaxes(showgrid=False, title_text=x_label or x)
        fig.update_yaxes(gridcolor="#e8e8e6", zerolinecolor="#d6d6d3", title_text=y_label or (y or ""))
        if chart_type == "barh":
            fig.update_xaxes(showgrid=True, gridcolor="#e8e8e6", title_text=y_label or (y or ""))
            fig.update_yaxes(showgrid=False, title_text=x_label or x)
    return fig


def build_chart(
    session: AnalysisSession,
    table: str,
    chart_type: str,
    x: str,
    y: str | None = None,
    value: str | None = None,
    color: str | None = None,
    agg: str | None = None,
    top_n: int | None = None,
    sort: str = "none",
    title: str = "",
    x_label: str | None = None,
    y_label: str | None = None,
    filter: str | None = None,  # noqa: A002 - 도구 스키마와 이름을 맞춘다
    chart_dir: Path | str = CHART_DIR,
) -> tuple[ChartRecord, list[str]]:
    df = session.get_table(table).copy()
    _require_columns(df, [x, y, color, value])
    data, y_used, notes = prepare_data(df, chart_type, x, y, color, value, agg, top_n, sort or "none", filter)
    fig = build_figure(data, chart_type, x, y_used, color, value, title, x_label, y_label, agg)
    chart_dir = Path(chart_dir)
    chart_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "table": table, "chart_type": chart_type, "x": x, "y": y, "value": value, "color": color, "agg": agg,
        "top_n": top_n, "sort": sort, "title": title, "x_label": x_label, "y_label": y_label, "filter": filter,
        "rows_plotted": int(len(data)),
    }
    rec = session.add_chart(kind="plotly", path="", title=title, table=table, spec=spec)
    path = chart_dir / f"{session.id}_{rec.id}.plotly.json"
    path.write_text(fig.to_json(), encoding="utf-8")
    rec.path = str(path)
    return rec, notes


def load_figure(path: str | Path) -> go.Figure:
    return pio.from_json(Path(path).read_text(encoding="utf-8"))


def figure_html(path: str | Path) -> str:
    fig = load_figure(path)
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, config={"displaylogo": False, "locale": "ko"})


def chart_summary(rec: ChartRecord) -> str:
    return json.dumps({"id": rec.id, "title": rec.title, "table": rec.table, "path": rec.path}, ensure_ascii=False)
