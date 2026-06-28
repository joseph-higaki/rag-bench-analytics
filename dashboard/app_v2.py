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


def accuracy_heatmap(
    cells: pd.DataFrame, row: str, col: str, row_title: str, col_title: str
) -> alt.LayerChart:
    """Red→yellow→green heatmap on an absolute [0,1] scale (0 red, 0.5 yellow, 1 green)."""
    col_field, col_sort = _qtype_axis(cells, col)
    _, row_sort = _retriever_axis(cells, row) if row == "display_label" else (row, "ascending")
    enc_x = alt.X(
        f"{col_field}:N",
        title=col_title,
        sort=col_sort,
        axis=alt.Axis(labelAngle=-45),  # diagonal so long type labels read fully
    )
    enc_y = alt.Y(
        f"{row}:N",
        title=row_title,
        sort=row_sort,
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
            alt.Tooltip(f"{col_field}:N", title=col_title),
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


# ──────────────────────────────────────────────────────────────────────────────
# Sections — one render_* per dashboard block, called from main(). Built section by
# section against the spec in next-prompts.md.env.
# ──────────────────────────────────────────────────────────────────────────────
def render_headline(df: pd.DataFrame) -> None:
    """Top-of-page KPI strip: pass rate, scored answers, cost per correct, best retriever.

    Whole-dataset rollups (no filtering) so the page opens on the totals. Cost per correct =
    Σ total_cost_usd / Σ passed — a ratio of two additive fact measures computed here at read
    time (a ratio is non-additive, so it's never stored on the fact); guarded to '—' when nothing
    passed (dividing by zero correct is undefined, not zero cost). Best retriever is the
    highest-accuracy condition with legacy graph_neighborhood excluded — it's hidden from the
    retriever comparison everywhere else (incl. the scatter below), so it must not win here. The
    other three cards are still whole-dataset totals (they're totals, not a comparison).
    """
    scored = len(df)
    pass_rate = df["passed"].mean() if scored else 0.0
    num_correct = int(df["passed"].sum())
    total_cost = df["total_cost_usd"].fillna(0).sum()
    cost_per_correct = f"${total_cost / num_correct:,.4f}" if num_correct else "—"

    ranked = df[~df["retriever"].isin(LEGACY_RETRIEVERS)]
    if len(ranked):
        ret_acc = ranked.groupby("display_label")["passed"].agg(correct="sum", n="size")
        ret_acc["accuracy"] = ret_acc["correct"] / ret_acc["n"]
        best_label = ret_acc["accuracy"].idxmax()
        best_acc = f"{ret_acc['accuracy'].max():.1%} accuracy"
    else:
        best_label, best_acc = "—", ""

    # 4th column gets extra width; its value is a long retriever label, not a number.
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1.3])
    c1.metric("Pass rate (overall accuracy)", f"{pass_rate:.1%}")
    c2.metric("Scored answers", f"{scored:,}")
    c3.metric(
        "Cost per correct (USD)",
        cost_per_correct,
        help="Total cost ÷ correct answers. A correct answer costs more than an attempt because "
        "wrong answers still cost money (cost/correct = cost/attempt ÷ accuracy).",
    )
    # Custom card (not st.metric): the value is a long retriever label that st.metric clips to one
    # nowrap line — this mirrors the metric look but lets the name wrap and show in full.
    c4.markdown(
        f"""
        <div style="line-height:1.3">
          <div style="font-size:0.875rem; opacity:0.6">Best retriever</div>
          <div style="font-size:1.5rem; font-weight:600">{best_label}</div>
          <div style="font-size:0.875rem; opacity:0.6">{best_acc}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_cost_per_correct_scatter(df: pd.DataFrame) -> None:
    """Combo — cost per correct (left axis) and accuracy (right axis) by retriever, sized by latency.

    Legacy graph_neighborhood is excluded (superseded by the _1hop/_2hop conditions); all generators
    pooled. The two series share the plot area on *independent* y-axes — the honest reading of
    "normalize accuracy onto the cost axis": accuracy keeps its own right-hand %% scale instead of
    being silently rescaled into dollars (a single shared dollar axis would make accuracy unreadable).
    Cost dots: y = Σ total_cost_usd / Σ passed, size = avg retrieval + avg generation latency (each
    phase averaged over its non-null answers then summed; errors emit no latency and are skipped).
    Accuracy: a dashed dot plot (passed/total). A zero-correct condition shows its accuracy dot but
    no cost dot — cost/correct is undefined, not zero.
    """
    keep = df[~df["retriever"].isin(LEGACY_RETRIEVERS)]
    by_ret = (
        keep.groupby(["display_label", "sort_order"], as_index=False)
        .agg(
            total_cost=("total_cost_usd", "sum"),
            correct=("passed", "sum"),
            total=("passed", "size"),
            avg_retrieval_ms=("retrieval_latency_ms", "mean"),
            avg_generation_ms=("generation_latency_ms", "mean"),
        )
        .sort_values("sort_order")  # data order = x order, so the dashed line tracks left→right
    )
    by_ret["accuracy"] = by_ret["correct"] / by_ret["total"]
    # .where(correct>0) makes the denominator NaN (not 0) when nothing passed, so the division
    # yields NaN — no divide-by-zero warning, and the cost dot drops out via dropna below.
    by_ret["cost_per_correct"] = by_ret["total_cost"] / by_ret["correct"].where(by_ret["correct"] > 0)
    # closed-book has no retrieval phase (avg is NaN) -> treat as 0 so its dot still sizes on gen.
    by_ret["avg_latency_ms"] = by_ret["avg_retrieval_ms"].fillna(0) + by_ret["avg_generation_ms"].fillna(0)

    st.subheader("Cost per correct answer by retriever")
    st.caption(
        "All generators pooled · legacy graph_neighborhood excluded.  \n"
        "Left axis = total cost ÷ correct answers (dot size = avg retrieval + generation latency); "
        "right axis = accuracy (dashed), normalized to share the plot.  \n"
        "Zero-correct conditions show accuracy only (cost per correct is undefined)."
    )

    _, ret_order = _retriever_axis(by_ret)
    x_enc = alt.X(
        "display_label:N", title="Retriever condition", sort=ret_order,
        axis=alt.Axis(labelAngle=-45),
    )
    # One shared color scale across both layers → a single 2-entry legend (blue cost / orange accuracy).
    series_color = alt.Color(
        "series:N", title=None,
        scale=alt.Scale(domain=["Cost per correct", "Accuracy"], range=["#4c78a8", "#f58518"]),
    )

    cost = (
        alt.Chart(by_ret.dropna(subset=["cost_per_correct"]).assign(series="Cost per correct"))
        .mark_circle(opacity=0.85)
        .encode(
            x=x_enc,
            y=alt.Y("cost_per_correct:Q", title="Cost per correct answer (USD)",
                    axis=alt.Axis(format="$,.4f")),
            size=alt.Size("avg_latency_ms:Q", title="Avg latency (ms)",
                          scale=alt.Scale(range=[100, 1200])),
            color=series_color,
            tooltip=[
                alt.Tooltip("display_label:N", title="Retriever"),
                alt.Tooltip("cost_per_correct:Q", title="Cost / correct", format="$,.4f"),
                alt.Tooltip("total_cost:Q", title="Total cost", format="$,.4f"),
                alt.Tooltip("correct:Q", title="Correct"),
                alt.Tooltip("total:Q", title="Answers"),
                alt.Tooltip("avg_latency_ms:Q", title="Avg latency (ms)", format=",.0f"),
            ],
        )
    )
    accuracy = (
        alt.Chart(by_ret.assign(series="Accuracy"))
        .mark_line(strokeDash=[4, 4], point=True)
        .encode(
            x=x_enc,
            y=alt.Y("accuracy:Q", title="Accuracy", scale=alt.Scale(domain=[0, 1]),
                    axis=alt.Axis(format=".0%")),
            color=series_color,
            tooltip=[
                alt.Tooltip("display_label:N", title="Retriever"),
                alt.Tooltip("accuracy:Q", title="Accuracy", format=".0%"),
                alt.Tooltip("correct:Q", title="Correct"),
                alt.Tooltip("total:Q", title="Answers"),
            ],
        )
    )
    chart = alt.layer(cost, accuracy).resolve_scale(y="independent").properties(height=380)
    st.altair_chart(chart, use_container_width=True)


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
    """SPARQL-gen accuracy by writer model family — heatmap of writer × question type.

    Isolates the one retriever with a second LLM in the loop (the SPARQL writer) and compares
    writer families head-to-head, holding the generator fixed. The line/grouped-bar encodings of
    this same slice are draft (single-series until more writer families land) and live in
    app-drafts-experiments.py.
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
    st.altair_chart(
        accuracy_heatmap(cells, "writer_model_family", "type_id",
                         "Writer model family", "Question type"),
        use_container_width=True,
    )


def render_latency_split(df: pd.DataFrame) -> None:
    """Retrieval vs generation latency per retriever condition (generator=haiku).

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
    st.subheader("Latency — retrieval vs generation by retriever condition")
    st.caption(
        f"Generator fixed to **{HAIKU_FAMILY}**, legacy retriever excluded · stacked bar = "
        "avg retrieval + avg generation latency (ms) per condition.  \n"
        "_Phases averaged over answers that emitted latency (errors skipped)._"
    )

    by_cond = keep.groupby(["display_label", "sort_order"], as_index=False).agg(
        Retrieval=("retrieval_latency_ms", "mean"),
        Generation=("generation_latency_ms", "mean"),
        n=("scored_answer_sk", "size"),
    )
    long = by_cond.melt(
        id_vars=["display_label", "sort_order", "n"],
        value_vars=["Retrieval", "Generation"],
        var_name="phase",
        value_name="avg_latency_ms",
    )
    # Pin stack/legend order so Retrieval is always the first (left) segment.
    long["phase_order"] = long["phase"].map({"Retrieval": 0, "Generation": 1})
    _, ret_order = _retriever_axis(by_cond)

    chart = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("avg_latency_ms:Q", title="Avg latency (ms)", stack="zero"),
            y=alt.Y(
                "display_label:N",
                title="Retriever condition",
                sort=ret_order,
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

    Reads dim_token_pricing (the conformed pricing dim, fed by int_model_pricing). Prices are USD
    per 1M tokens; each row's source (portkey | override) is shown so a cost is traceable to its
    rate. No computation here — cost is precomputed in dbt; this only displays the inputs (rule #2:
    the dashboard reads marts, it doesn't price).
    """
    st.subheader("Model token pricing — reference & provenance")
    sources = ", ".join(sorted(dim_pricing["pricing_source"].dropna().unique())) or "—"
    st.caption(
        f"USD per 1M tokens · source of record: **{sources}** "
        "(Portkey snapshot, refreshed by `make refresh-pricing`; override for local Ollama).  \n"
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
    st.set_page_config(page_title="Biomedical RAG Bench — Analytics", layout="wide")
    st.title("Biomedical RAG Bench — Retriever Analytics")

    dim_q = load_mart("dim_question")
    dim_pricing = load_mart("dim_token_pricing")
    df = load_analysis()

    render_headline(df)
    st.divider()
    render_cost_per_correct_scatter(df)
    st.divider()
    render_accuracy_matrix1(df)
    st.divider()
    render_accuracy_matrix2(df)
    st.divider()
    render_latency_split(df)
    st.divider()
    render_ground_truth(dim_q)
    st.divider()
    render_pricing_reference(dim_pricing)


if __name__ == "__main__":
    main()
