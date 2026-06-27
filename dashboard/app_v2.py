"""Streamlit dashboard v2 — reads the marts schema directly via a read-only role (rule #2)."""

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
    x = alt.X("type_id:N", title="Question type", sort="ascending",
              axis=alt.Axis(labelAngle=-45))
    color = alt.Color(f"{series_col}:N", title=series_title, sort="ascending")
    # Solid vs dashed distinguishes the two measures; explicit domain pins the mapping so the
    # accuracy layer is always solid regardless of layer/encoding order.
    dash = alt.StrokeDash(
        "metric:N", title="Measure",
        scale=alt.Scale(domain=["Accuracy", "Avg tokens"], range=[[1, 0], [5, 4]]),
    )
    tooltip = [
        alt.Tooltip("type_id:N", title="Question type"),
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
    # Labels come from seed_question_type_labels; fall back to the raw id if a type is
    # unseeded so the grid never goes blank. type_id stays as the compact "Type" column —
    # it's the join key the accuracy heatmaps below put on their axes.
    examples["type_display_label"] = examples["type_display_label"].fillna(examples["type_id"])
    examples["type_description"] = examples["type_description"].fillna("—")

    display = examples[
        [
            "type_id",
            "type_display_label",
            "type_description",
            "n_questions",
            "question_text",
            "ground_truth_answer_text",
        ]
    ].rename(
        columns={
            "type_id": "Type",
            "type_display_label": "Question type",
            "type_description": "What it tests",
            "n_questions": "# questions",
            "question_text": "Question example",
            "ground_truth_answer_text": "Ground truth example",
        }
    )
    st.dataframe(display, width="stretch", hide_index=True)


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


def render_latency_split(df: pd.DataFrame) -> None:
    """DRAFT — retrieval vs generation latency per retriever condition (generator=haiku).

    Stacked bars split each condition's avg wall-clock into its two phases: retrieval (the
    retriever fetching context) and generation (the LLM answering). Generator is held fixed
    so the split is attributable to the retriever — closed-book pays ~0 retrieval, graph
    conditions pay for traversal, and generation tracks the context the retriever fed it.
    Each phase is averaged over its non-null answers (error rows emit no latency and are
    skipped), so a bar is the typical breakdown, not a coalesced-to-0 sum.
    """
    keep = df[
        (df["generator_model_family"] == HAIKU_FAMILY)
        & (~df["retriever"].isin(LEGACY_RETRIEVERS))
    ]
    st.subheader("🚧 DRAFT · Latency — retrieval vs generation by retriever condition")
    st.caption(
        f"Generator fixed to **{HAIKU_FAMILY}**, legacy retriever excluded · stacked bar = "
        "avg retrieval + avg generation latency (ms) per condition.  \n"
        "_Draft for review — phases averaged over answers that emitted latency (errors skipped)._"
    )

    by_cond = keep.groupby("display_label", as_index=False).agg(
        Retrieval=("retrieval_latency_ms", "mean"),
        Generation=("generation_latency_ms", "mean"),
        n=("scored_answer_sk", "size"),
    )
    long = by_cond.melt(
        id_vars=["display_label", "n"],
        value_vars=["Retrieval", "Generation"],
        var_name="phase",
        value_name="avg_latency_ms",
    )
    # Pin stack/legend order so Retrieval is always the first (left) segment.
    long["phase_order"] = long["phase"].map({"Retrieval": 0, "Generation": 1})

    chart = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("avg_latency_ms:Q", title="Avg latency (ms)", stack="zero"),
            y=alt.Y(
                "display_label:N",
                title="Retriever condition",
                sort=alt.EncodingSortField("avg_latency_ms", op="sum", order="descending"),
            ),
            color=alt.Color(
                "phase:N",
                title="Phase",
                sort=["Retrieval", "Generation"],
                scale=alt.Scale(
                    domain=["Retrieval", "Generation"], range=["#4c78a8", "#f58518"]
                ),
            ),
            order=alt.Order("phase_order:Q"),
            tooltip=[
                alt.Tooltip("display_label:N", title="Retriever condition"),
                alt.Tooltip("phase:N", title="Phase"),
                alt.Tooltip("avg_latency_ms:Q", title="Avg latency (ms)", format=",.0f"),
                alt.Tooltip("n:Q", title="Answers"),
            ],
        )
        .properties(height=alt.Step(40))
    )
    st.altair_chart(chart, use_container_width=True)


def render_pricing_reference(dim_pricing: pd.DataFrame) -> None:
    """Reference + provenance: the token prices behind every cost figure above.

    Reads dim_token_pricing (the conformed pricing dim, fed by the int_model_pricing swap point).
    Prices are USD per 1M tokens; each row's source (seed | portkey | override) is shown so a cost
    is traceable to its rate. No computation here — cost is precomputed in dbt; this only displays
    the inputs (rule #2: the dashboard reads marts, it doesn't price).
    """
    st.subheader("Model token pricing — reference & provenance")
    sources = ", ".join(sorted(dim_pricing["pricing_source"].dropna().unique())) or "—"
    st.caption(
        f"USD per 1M tokens · source of record: **{sources}** "
        "(swap with `dbt build --vars pricing_source=portkey`).  \n"
        "These are the rates behind the cost figures above — cost is computed in dbt, not here."
    )
    display = dim_pricing.sort_values(["provider", "model_resolved"])[
        [
            "provider", "model_resolved",
            "input_usd_per_mtok", "output_usd_per_mtok",
            "cache_read_usd_per_mtok", "cache_write_usd_per_mtok",
            "effective_date", "pricing_source",
        ]
    ].rename(
        columns={
            "provider": "Provider", "model_resolved": "Model",
            "input_usd_per_mtok": "Input $/Mtok", "output_usd_per_mtok": "Output $/Mtok",
            "cache_read_usd_per_mtok": "Cache-read $/Mtok",
            "cache_write_usd_per_mtok": "Cache-write $/Mtok",
            "effective_date": "As of", "pricing_source": "Source",
        }
    )
    st.dataframe(
        display.style.format({
            "Input $/Mtok": "${:.2f}", "Output $/Mtok": "${:.2f}",
            "Cache-read $/Mtok": "${:.2f}", "Cache-write $/Mtok": "${:.2f}",
        }),
        width="stretch", hide_index=True,
    )


def main() -> None:
    st.set_page_config(page_title="Biomedical RAG Bench — Analytics v2", layout="wide")
    st.title("Biomedical RAG Bench — Retriever Analytics v2")

    dim_q = load_mart("dim_question")
    dim_pricing = load_mart("dim_token_pricing")
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
    st.divider()
    render_latency_split(df)
    st.divider()
    render_pricing_reference(dim_pricing)


if __name__ == "__main__":
    main()
