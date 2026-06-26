"""Streamlit dashboard v2 — reads the marts schema directly via a read-only role (rule #2)."""

from __future__ import annotations

import os

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

# Swappable y-measure for the per-question-type series charts. domain=None lets the axis
# autoscale (token counts); accuracy is pinned to an absolute 0–100%. fmt is a Vega format.
ACCURACY_METRIC = {"field": "accuracy", "title": "Accuracy", "fmt": "%", "domain": [0, 1]}
TOKENS_METRIC = {
    "field": "avg_tokens", "title": "Avg total tokens / answer", "fmt": "~s", "domain": None
}
METRICS = {"Accuracy": ACCURACY_METRIC, "Avg total tokens / answer": TOKENS_METRIC}


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
    with _marts_conn() as conn:
        return pd.read_sql(f'select * from "{MARTS_SCHEMA}"."{table}"', conn)


@st.cache_data(ttl=300)
def load_analysis() -> pd.DataFrame:
    """One denormalized fact for the scoring sections: fct + the dims each view slices on.

    `passed` is the accuracy numerator at the row grain: is_passed==True → 1, else 0 — so a
    null (ungraded) or errored answer counts as not-passed. Cell accuracy is then mean(passed)
    = passed/total over the cell (the spec's count(is_passed=true)/total).
    """
    fct = load_mart("fct_scored_answer")
    dim_q = load_mart("dim_question")[["question_sk", "type_id", "question_hop_count"]]
    dim_ret = load_mart("dim_retriever_cond")[
        ["retriever_cond_sk", "display_label", "retriever", "mechanism"]
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
    df["passed"] = (df["is_passed"] == True).fillna(False).astype(int)  # noqa: E712
    # total tokens for the answer pipeline: generator + (SPARQL) writer, nulls as 0.
    df["total_tokens"] = (
        df["generator_total_tokens"].fillna(0) + df["writer_total_tokens"].fillna(0)
    )
    return df


def accuracy_cells(df: pd.DataFrame, row: str, col: str) -> pd.DataFrame:
    """Long-form per (row, col) cell: accuracy (passed/total), avg tokens, and the counts."""
    cells = df.groupby([row, col], as_index=False).agg(
        total=("passed", "size"),
        passed=("passed", "sum"),
        avg_tokens=("total_tokens", "mean"),
    )
    cells["accuracy"] = cells["passed"] / cells["total"]
    return cells


def accuracy_heatmap(
    cells: pd.DataFrame, row: str, col: str, row_title: str, col_title: str
) -> alt.LayerChart:
    """Red→yellow→green heatmap on an absolute [0,1] scale (0 red, 0.5 yellow, 1 green)."""
    enc_x = alt.X(
        f"{col}:N",
        title=col_title,
        sort="ascending",
        axis=alt.Axis(labelAngle=-45),  # diagonal so long type labels read fully
    )
    enc_y = alt.Y(
        f"{row}:N",
        title=row_title,
        sort="ascending",
        # Wrap each label at its " (" parenthetical onto a 2nd line (no-op if no paren).
        axis=alt.Axis(
            labelExpr="split(replace(datum.label, ' (', '\\n('), '\\n')", labelLimit=200
        ),
    )
    base = alt.Chart(cells)
    heat = base.mark_rect().encode(
        x=enc_x,
        y=enc_y,
        color=alt.Color(
            "accuracy:Q",
            title="Accuracy",
            scale=alt.Scale(scheme="redyellowgreen", domain=[0, 1]),
        ),
        tooltip=[
            alt.Tooltip(f"{row}:N", title=row_title),
            alt.Tooltip(f"{col}:N", title=col_title),
            alt.Tooltip("accuracy:Q", title="Accuracy", format=".0%"),
            alt.Tooltip("passed:Q", title="Passed"),
            alt.Tooltip("total:Q", title="Total"),
        ],
    )
    labels = base.mark_text(baseline="middle", fontSize=11).encode(
        x=enc_x,
        y=enc_y,
        text=alt.Text("accuracy:Q", format=".0%"),
        # White reads better on the dark red/green extremes, black on the yellow middle.
        color=alt.condition(
            "datum.accuracy < 0.25 || datum.accuracy > 0.85",
            alt.value("white"),
            alt.value("black"),
        ),
    )
    return (heat + labels).properties(height=alt.Step(38))


def _series_encodings(series_col: str, series_title: str, metric: dict) -> dict:
    """Shared x/y/color/tooltip for the per-question-type series charts (line + grouped bar).

    `metric` swaps the y-measure (accuracy vs tokens). color sort is pinned so the same
    `series_col` value keeps its colour across charts; the tooltip carries both measures.
    """
    y_kwargs = {"title": metric["title"], "axis": alt.Axis(format=metric["fmt"])}
    if metric["domain"] is not None:
        y_kwargs["scale"] = alt.Scale(domain=metric["domain"])
    return dict(
        x=alt.X("type_id:N", title="Question type", sort="ascending",
                axis=alt.Axis(labelAngle=-45)),
        y=alt.Y(f"{metric['field']}:Q", **y_kwargs),
        color=alt.Color(f"{series_col}:N", title=series_title, sort="ascending"),
        tooltip=[
            alt.Tooltip("type_id:N", title="Question type"),
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
        **_series_encodings(series_col, series_title, metric)
    )


def series_grouped_bar_chart(
    cells: pd.DataFrame, series_col: str, series_title: str, metric: dict = ACCURACY_METRIC
) -> alt.Chart:
    """Grouped bars per question type, one bar per `series_col` value, y = the chosen metric."""
    return alt.Chart(cells).mark_bar().encode(
        xOffset=alt.XOffset(f"{series_col}:N"),
        **_series_encodings(series_col, series_title, metric),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Sections — one render_* per dashboard block, called from main(). Built section by
# section against the spec in next-prompts.md.env.
# ──────────────────────────────────────────────────────────────────────────────
def render_ground_truth(dim_q: pd.DataFrame) -> None:
    """Explainer table: one representative question + its graph-derived answer, per type.

    Orients the reader before any scoring: what the 10 question types look like and what
    'correct' means. Ground truth is graph traversal, never an LLM (CLAUDE.md semantics).
    """
    st.subheader("Ground truth — what each question type asks")
    st.caption(
        "One example per question type, with the answer derived from graph traversal "
        "(never an LLM). Types differ in count, so later accuracy is computed per type."
    )

    # One coherent example row per type: sort, then keep the first whole row per type.
    # (drop_duplicates keeps the row intact; groupby().first() would take the first
    # non-null value *per column independently* — risking a stitched-together example.)
    examples = (
        dim_q.sort_values(["type_id", "question_id"])
        .drop_duplicates(subset="type_id", keep="first")
        .copy()
    )
    examples["n_questions"] = examples["type_id"].map(dim_q.groupby("type_id").size())
    # Unanswerable types have empty ground truth (correct: no entity is the answer).
    examples["ground_truth_answer_text"] = examples["ground_truth_answer_text"].fillna(
        "— (unanswerable)"
    )

    display = examples[
        ["type_id", "n_questions", "question_text", "ground_truth_answer_text"]
    ].rename(
        columns={
            "type_id": "Question type",
            "n_questions": "# questions",
            "question_text": "Question example",
            "ground_truth_answer_text": "Ground truth example",
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_accuracy_matrix1(df: pd.DataFrame) -> None:
    """Heatmap: accuracy by retriever × question type; generator=haiku, writers pooled."""
    haiku = df[
        (df["generator_model_family"] == HAIKU_FAMILY)
        & (~df["retriever"].isin(LEGACY_RETRIEVERS))
    ]
    # List the SPARQL-writer models actually pooled (non-SPARQL retrievers have no writer).
    writers = ", ".join(sorted(haiku["writer_model"].dropna().unique())) or "—"

    st.subheader("Accuracy by retriever × question type")
    st.caption(
        f"Generator fixed to **{HAIKU_FAMILY}**, all writers pooled.  \n"
        f"Writer = all (SPARQL-gen): {writers} · none for non-SPARQL retrievers.  \n"
        "Accuracy = passed / total answers in the cell (errors & ungraded count as not-passed)."
    )

    cells = accuracy_cells(haiku, row="display_label", col="type_id")
    chart = accuracy_heatmap(
        cells, row="display_label", col="type_id",
        row_title="Retriever condition", col_title="Question type",
    )
    st.altair_chart(chart, use_container_width=True)


def render_accuracy_matrix2(df: pd.DataFrame) -> None:
    """SPARQL-gen accuracy by writer model family — three views (2.0 heatmap, 2.1 line, 2.2 bar).

    Isolates the one retriever with a second LLM in the loop (the SPARQL writer) and compares
    writer families head-to-head, holding the generator fixed. Same slice, three encodings.
    """
    sparql = df[
        (df["generator_model_family"] == HAIKU_FAMILY)
        & (df["retriever"] == "graph_sparqlgen")
    ]
    writers = ", ".join(sorted(sparql["writer_model_family"].dropna().unique())) or "—"

    st.subheader("SPARQL-gen accuracy by writer model family")
    st.caption(
        f"Graph SPARQL-generation retriever only · generator fixed to **{HAIKU_FAMILY}**.  \n"
        f"Writer families present: {writers} (qwen is only a *generator*, never a writer).  \n"
        "Accuracy = passed / total answers in the cell (errors & ungraded count as not-passed)."
    )

    cells = accuracy_cells(sparql, row="writer_model_family", col="type_id")

    st.markdown("**2.0 — heatmap** · writer × question type")
    st.altair_chart(
        accuracy_heatmap(cells, "writer_model_family", "type_id",
                         "Writer model family", "Question type"),
        use_container_width=True,
    )

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
    """Per-question-type accuracy (or tokens) across harness versions; generator+writer=haiku.

    The y-measure toggles between accuracy and avg tokens/answer (a metric switch, not a dual
    axis — two arbitrary scales on one chart would invite false correlation).
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
        f"series = harness version (present: {', '.join(versions) or '—'})."
    )
    if len(versions) < 2:
        st.info(
            f"Only **{versions[0] if versions else '—'}** is present — no evolution to plot yet. "
            "This chart turns into a comparison automatically once harness-v2/v3 runs are ingested."
        )

    metric_label = st.radio(
        "Y axis", list(METRICS), horizontal=True, key="harness_metric"
    )
    cells = accuracy_cells(keep, row="harness_version", col="type_id")
    st.altair_chart(
        series_line_chart(cells, "harness_version", "Harness version", METRICS[metric_label]),
        use_container_width=True,
    )


def render_token_usage(df: pd.DataFrame) -> None:
    """Avg total tokens per answered question, by question type × retriever (generator=haiku).

    Averaged per answer, never summed: a type with 8 questions shouldn't look costlier than a
    4-question type just for having more of them — the per-question cost is what's comparable.
    total tokens = input + output across the generator and (for SPARQL-gen) the writer.
    Retriever is the series because it dominates token cost (closed-book is cheap, graph is not),
    so pooling retrievers into one line would blend away the very thing worth seeing.
    """
    keep = df[
        (df["generator_model_family"] == HAIKU_FAMILY)
        & (~df["retriever"].isin(LEGACY_RETRIEVERS))
    ]
    st.subheader("Token usage — avg total tokens per answered question")
    st.caption(
        f"Generator fixed to **{HAIKU_FAMILY}**, writers pooled, legacy retriever excluded.  \n"
        "Total tokens = input + output across generator + SPARQL writer, **averaged per "
        "answered question** so differing question counts per type don't distort the cost."
    )
    cells = accuracy_cells(keep, row="display_label", col="type_id")
    st.altair_chart(
        series_line_chart(cells, "display_label", "Retriever condition", TOKENS_METRIC),
        use_container_width=True,
    )


def main() -> None:
    st.set_page_config(page_title="Biomedical RAG Bench — Analytics v2", layout="wide")
    st.title("Biomedical RAG Bench — Retriever Analytics v2")

    dim_q = load_mart("dim_question")
    df = load_analysis()

    render_ground_truth(dim_q)
    st.divider()
    render_accuracy_matrix1(df)
    st.divider()
    render_accuracy_matrix2(df)
    st.divider()
    render_harness_evolution(df)
    st.divider()
    render_token_usage(df)


if __name__ == "__main__":
    main()
