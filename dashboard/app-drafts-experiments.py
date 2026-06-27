"""Streamlit DRAFTS / experiments scratchpad — reads marts via the read-only role (rule #2).

A testing surface for dashboard sections that aren't ready for the official app (`app_v2.py`):
single-series or aspirational views that only become meaningful once more data lands (multiple
writer families, harness v2/v3, etc.). Deliberately a **self-contained copy** of app_v2's
loading + charting machinery — not an import — so chart code can be hacked on freely here
without touching or breaking the official app. Promote a section back into app_v2 once it earns
its place; let the two drift in the meantime.

Currently parked here:
  • Token usage & accuracy — per answered question   (dual-axis, retriever series)
  • Harness-version evolution                          (dual-axis, single series until v2/v3)
  • SPARQL-gen writer series — 2.1 line / 2.2 grouped bar (single writer family for now)
"""

from __future__ import annotations

import os
from decimal import Decimal

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

MARTS_SCHEMA = f"{os.environ.get('DBT_SCHEMA', 'analytics')}_marts"

# Family label for the fixed generator the spec pins most views to ("generator = haiku").
HAIKU_FAMILY = "claude-haiku-4-5"

# The old single-config graph_neighborhood condition — superseded by the _1hop/_2hop
# conditions; filtered out of comparisons as legacy noise.
LEGACY_RETRIEVERS = ("graph_neighborhood",)

# Y-measure for the single-axis per-question-type series charts (line + grouped bar, 2.1/2.2).
# Accuracy is pinned to an absolute 0–100%; fmt is a Vega format. The dual-axis charts further
# down carry accuracy *and* tokens together and don't go through this.
ACCURACY_METRIC = {"field": "accuracy", "title": "Accuracy", "fmt": "%", "domain": [0, 1]}


def _marts_conn() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "analytics"),
        user=os.environ.get("MARTS_READER_USER", "marts_reader"),
        password=os.environ.get("MARTS_READER_PASSWORD", "marts_reader"),
    )


@st.cache_data(ttl=300)
def load_mart(table: str) -> pd.DataFrame:
    # Build from the cursor, not pd.read_sql (which warns on a raw psycopg conn — it wants a
    # SQLAlchemy engine we deliberately don't add). Postgres numeric -> Decimal; coerce to float
    # to match read_sql's float64 dtype so downstream means/charts behave identically.
    with _marts_conn() as conn:
        cur = conn.execute(f'select * from "{MARTS_SCHEMA}"."{table}"')
        df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = df[col].astype(float)
    return df


@st.cache_data(ttl=300)
def load_analysis() -> pd.DataFrame:
    """One denormalized fact for the scoring sections: fct + the dims each view slices on.

    `passed` is the accuracy numerator at the row grain: is_passed==True → 1, else 0 — so a
    null (ungraded) or errored answer counts as not-passed. Cell accuracy is then mean(passed)
    = passed/total over the cell (the spec's count(is_passed=true)/total).
    """
    fct = load_mart("fct_scored_answer")
    dim_q = load_mart("dim_question")[
        ["question_sk", "type_id", "type_display_label", "question_hop_count"]
    ]
    dim_ret = load_mart("dim_retriever_cond")[
        ["retriever_cond_sk", "display_label", "retriever", "mechanism", "sort_order"]
    ]
    dim_gen = load_mart("dim_generator")[["generator_sk", "generator_model_family"]]
    dim_writer = load_mart("dim_writer")[
        ["writer_sk", "writer_model", "writer_model_family"]
    ]
    dim_run = load_mart("dim_run")[["run_sk", "harness_version"]]
    df = (
        fct.merge(dim_q, on="question_sk", how="left")
        .merge(dim_ret, on="retriever_cond_sk", how="left")
        .merge(dim_gen, on="generator_sk", how="left")
        .merge(dim_writer, on="writer_sk", how="left")
        .merge(dim_run, on="run_sk", how="left")
    )
    # Never leave the question-type axis blank if a type_id is unseeded — fall back to the id.
    df["type_display_label"] = df["type_display_label"].fillna(df["type_id"])
    df["passed"] = (df["is_passed"] == True).fillna(False).astype(int)  # noqa: E712
    # total tokens for the answer pipeline: generator + (SPARQL) writer, nulls as 0.
    df["total_tokens"] = (
        df["generator_total_tokens"].fillna(0) + df["writer_total_tokens"].fillna(0)
    )
    return df


def accuracy_cells(df: pd.DataFrame, row: str, col: str) -> pd.DataFrame:
    """Long-form per (row, col) cell: accuracy (passed/total), avg tokens, and the counts.

    When the column axis is the question type, its display label rides along (1:1 with type_id,
    so the grain is unchanged) — charts show "Factoid (1-hop)" yet still sort by numbered type_id.
    """
    keys = [row, col]
    if col == "type_id" and "type_display_label" in df.columns:
        keys.append("type_display_label")
    if row == "display_label" and "sort_order" in df.columns:
        keys.append("sort_order")
    cells = df.groupby(keys, as_index=False).agg(
        total=("passed", "size"),
        passed=("passed", "sum"),
        avg_tokens=("total_tokens", "mean"),
    )
    cells["accuracy"] = cells["passed"] / cells["total"]
    return cells


def _qtype_axis(cells: pd.DataFrame, col: str = "type_id") -> tuple[str, object]:
    """(field, sort) for the question-type axis: show the display label, order by numbered type_id.

    Keeps the 01→10 question-type order (an explicit category list, since the labels themselves
    sort alphabetically). Falls back to the raw `col`/ascending when labels aren't present, so
    non-type axes and unlabelled data still render.
    """
    if "type_display_label" in cells.columns:
        order = (
            cells[["type_id", "type_display_label"]]
            .drop_duplicates()
            .sort_values("type_id")["type_display_label"]
            .tolist()
        )
        return "type_display_label", order
    return col, "ascending"


def _retriever_axis(cells: pd.DataFrame, field: str = "display_label") -> tuple[str, object]:
    """(field, sort) for the retriever axis: canonical order from sort_order (seed-driven)."""
    if "sort_order" in cells.columns and field in cells.columns:
        order = (
            cells[[field, "sort_order"]]
            .drop_duplicates(field)
            .sort_values("sort_order")[field]
            .tolist()
        )
        return field, order
    return field, "ascending"


def _series_encodings(cells: pd.DataFrame, series_col: str, series_title: str, metric: dict) -> dict:
    """Shared x/y/color/tooltip for the per-question-type series charts (line + grouped bar).

    `metric` swaps the y-measure (accuracy vs tokens). color sort is pinned so the same
    `series_col` value keeps its colour across charts; the tooltip carries both measures.
    """
    y_kwargs = {"title": metric["title"], "axis": alt.Axis(format=metric["fmt"])}
    if metric["domain"] is not None:
        y_kwargs["scale"] = alt.Scale(domain=metric["domain"])
    xf, xs = _qtype_axis(cells)
    _, color_sort = _retriever_axis(cells, series_col) if series_col == "display_label" else (series_col, "ascending")
    return dict(
        x=alt.X(f"{xf}:N", title="Question type", sort=xs,
                axis=alt.Axis(labelAngle=-45)),
        y=alt.Y(f"{metric['field']}:Q", **y_kwargs),
        color=alt.Color(f"{series_col}:N", title=series_title, sort=color_sort),
        tooltip=[
            alt.Tooltip(f"{xf}:N", title="Question type"),
            alt.Tooltip(f"{series_col}:N", title=series_title),
            alt.Tooltip("accuracy:Q", title="Accuracy", format=".0%"),
            alt.Tooltip("avg_tokens:Q", title="Avg tokens/answer", format=",.0f"),
            alt.Tooltip("total:Q", title="Answers"),
        ],
    )


def series_line_chart(
    cells: pd.DataFrame, series_col: str, series_title: str, metric: dict = ACCURACY_METRIC
) -> alt.Chart:
    """One line per `series_col` value vs question type, y = the chosen metric."""
    return alt.Chart(cells).mark_line(point=True).encode(
        **_series_encodings(cells, series_col, series_title, metric)
    )


def series_grouped_bar_chart(
    cells: pd.DataFrame, series_col: str, series_title: str, metric: dict = ACCURACY_METRIC
) -> alt.Chart:
    """Grouped bars per question type, one bar per `series_col` value, y = the chosen metric."""
    return alt.Chart(cells).mark_bar().encode(
        xOffset=alt.XOffset(f"{series_col}:N"),
        **_series_encodings(cells, series_col, series_title, metric),
    )


def series_dual_axis_chart(
    cells: pd.DataFrame, series_col: str, series_title: str
) -> alt.LayerChart:
    """Accuracy (left axis, %) + avg tokens (right axis) on one chart, per question type.

    A deliberate dual axis: the two measures keep *independent* y-scales (resolve_scale
    y='independent') so each fills the plot height — the "normalized" view that compares shape
    without forcing two unrelated units onto one number line. colour = series; line style =
    measure (solid accuracy / dashed tokens). Both axes are zero-anchored so the visual gap
    between the lines isn't an artefact of a floating baseline. Caveat carried over from the
    old metric-toggle: a dual axis can *imply* a correlation that isn't there — read each line
    against its own axis, never against the other.
    """
    xf, xs = _qtype_axis(cells)
    _, color_sort = _retriever_axis(cells, series_col) if series_col == "display_label" else (series_col, "ascending")
    x = alt.X(f"{xf}:N", title="Question type", sort=xs,
              axis=alt.Axis(labelAngle=-45))
    color = alt.Color(f"{series_col}:N", title=series_title, sort=color_sort)
    # Solid vs dashed distinguishes the two measures; explicit domain pins the mapping so the
    # accuracy layer is always solid regardless of layer/encoding order.
    dash = alt.StrokeDash(
        "metric:N", title="Measure",
        scale=alt.Scale(domain=["Accuracy", "Avg tokens"], range=[[1, 0], [5, 4]]),
    )
    tooltip = [
        alt.Tooltip(f"{xf}:N", title="Question type"),
        alt.Tooltip(f"{series_col}:N", title=series_title),
        alt.Tooltip("accuracy:Q", title="Accuracy", format=".0%"),
        alt.Tooltip("avg_tokens:Q", title="Avg tokens/answer", format=",.0f"),
        alt.Tooltip("total:Q", title="Answers"),
    ]
    accuracy = (
        alt.Chart(cells.assign(metric="Accuracy"))
        .mark_line(point=True)
        .encode(
            x=x,
            y=alt.Y("accuracy:Q", title="Accuracy",
                    scale=alt.Scale(domain=[0, 1]),
                    axis=alt.Axis(format="%", orient="left")),
            color=color, strokeDash=dash, tooltip=tooltip,
        )
    )
    tokens = (
        alt.Chart(cells.assign(metric="Avg tokens"))
        .mark_line(point=True)
        .encode(
            x=x,
            y=alt.Y("avg_tokens:Q", title="Avg total tokens / answer",
                    scale=alt.Scale(zero=True),
                    axis=alt.Axis(format="~s", orient="right")),
            color=color, strokeDash=dash, tooltip=tooltip,
        )
    )
    return alt.layer(accuracy, tokens).resolve_scale(y="independent")


# ──────────────────────────────────────────────────────────────────────────────
# Draft sections — moved out of app_v2.py while they're single-series / aspirational.
# ──────────────────────────────────────────────────────────────────────────────
def render_writer_series(df: pd.DataFrame) -> None:
    """SPARQL-gen accuracy by writer family — line (2.1) + grouped bar (2.2) of the same slice.

    Companion encodings to app_v2's 2.0 heatmap: only the graph SPARQL-gen retriever has a
    second LLM (the writer) in the loop. Draft until more than one writer family is present —
    today these collapse to a single series.
    """
    sparql = df[
        (df["generator_model_family"] == HAIKU_FAMILY)
        & (df["retriever"] == "graph_sparqlgen")
    ]
    writers = ", ".join(sorted(sparql["writer_model_family"].dropna().unique())) or "—"

    st.subheader("SPARQL-gen accuracy by writer model family — series views")
    st.caption(
        f"Graph SPARQL-generation retriever only · generator fixed to **{HAIKU_FAMILY}**.  \n"
        f"Writer families present: {writers} (qwen is only a *generator*, never a writer).  \n"
        "Accuracy = passed / total answers in the cell (errors & ungraded count as not-passed)."
    )

    cells = accuracy_cells(sparql, row="writer_model_family", col="type_id")

    st.markdown("**2.1 — line** · accuracy by question type, one line per writer")
    st.altair_chart(
        series_line_chart(cells, "writer_model_family", "Writer family"),
        use_container_width=True,
    )

    st.markdown("**2.2 — grouped bars** · accuracy per question type, one bar per writer")
    st.altair_chart(
        series_grouped_bar_chart(cells, "writer_model_family", "Writer family"),
        use_container_width=True,
    )


def render_harness_evolution(df: pd.DataFrame) -> None:
    """Per-question-type accuracy + avg tokens across harness versions; generator+writer=haiku.

    Accuracy (left axis, %) and avg tokens/answer (right axis) share one chart on independent,
    zero-anchored scales (the "normalized" dual axis) so a version's quality and its cost read
    together per question type. Series = harness version. Replaces the earlier accuracy/tokens
    metric toggle — read each line against its own axis; their proximity isn't a correlation.
    """
    keep = df[
        (df["generator_model_family"] == HAIKU_FAMILY)
        & (~df["retriever"].isin(LEGACY_RETRIEVERS))
        & (
            df["writer_model"].isna()
            | df["writer_model"].str.startswith(HAIKU_FAMILY, na=False)
        )
    ]
    versions = sorted(keep["harness_version"].dropna().unique())

    st.subheader("Harness-version evolution")
    st.caption(
        f"Generator + writer fixed to **{HAIKU_FAMILY}**, retrieval conditions pooled · "
        f"series = harness version (present: {', '.join(versions) or '—'}).  \n"
        "Accuracy (left axis, %, solid) and avg total tokens/answer (right axis, dashed) on "
        "independent zero-anchored scales — read each line against its own axis."
    )
    if len(versions) < 2:
        st.info(
            f"Only **{versions[0] if versions else '—'}** is present — no evolution to plot yet. "
            "This chart turns into a comparison automatically once harness-v2/v3 runs are ingested."
        )

    cells = accuracy_cells(keep, row="harness_version", col="type_id")
    st.altair_chart(
        series_dual_axis_chart(cells, "harness_version", "Harness version"),
        use_container_width=True,
    )


def render_token_usage(df: pd.DataFrame) -> None:
    """Avg tokens (right axis) + accuracy (left axis) per question type × retriever; gen=haiku.

    Cost and quality on one dual-axis chart, independent zero-anchored scales (the "normalized"
    view). Tokens are averaged per answer, never summed: a type with 8 questions shouldn't look
    costlier than a 4-question type just for having more of them — per-question cost is what's
    comparable. total tokens = input + output across the generator and (for SPARQL-gen) the
    writer. Retriever is the series because it dominates token cost (closed-book is cheap, graph
    is not); each condition gets a solid accuracy line and a dashed token line — read each
    against its own axis, the two aren't a correlation.
    """
    keep = df[
        (df["generator_model_family"] == HAIKU_FAMILY)
        & (~df["retriever"].isin(LEGACY_RETRIEVERS))
    ]
    st.subheader("Token usage & accuracy — per answered question")
    st.caption(
        f"Generator fixed to **{HAIKU_FAMILY}**, writers pooled, legacy retriever excluded.  \n"
        "Accuracy (left axis, %, solid) and total tokens = input + output across generator + "
        "SPARQL writer (right axis, dashed), **averaged per answered question** so differing "
        "question counts per type don't distort the comparison.  \n"
        "_Dual axis: read each line against its own axis — proximity isn't correlation._"
    )
    cells = accuracy_cells(keep, row="display_label", col="type_id")
    st.altair_chart(
        series_dual_axis_chart(cells, "display_label", "Retriever condition"),
        use_container_width=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="Biomedical RAG Bench — Analytics (drafts/experiments)", layout="wide"
    )
    st.title("Biomedical RAG Bench — Drafts & experiments")
    st.caption(
        "Scratchpad for dashboard sections not yet promoted to the official app (`app_v2.py`). "
        "Self-contained on purpose — hack freely; promote winners back when they earn it."
    )

    df = load_analysis()

    render_token_usage(df)
    st.divider()
    render_harness_evolution(df)
    st.divider()
    render_writer_series(df)


if __name__ == "__main__":
    main()
