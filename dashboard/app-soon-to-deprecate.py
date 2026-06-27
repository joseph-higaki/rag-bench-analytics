"""Streamlit dashboard — reads the marts schema directly via a read-only role (rule #2).

Connects to <DBT_SCHEMA>_marts over a least-privilege `marts_reader` role (ADR-001),
never raw/staging and never as the analytics owner. The analytic story: the generator is
fixed per run, ground truth is graph traversal — so the compared variable is the
RETRIEVER. Every view slices outcomes/cost/latency by retriever condition.
"""

from __future__ import annotations

import os

import pandas as pd
import psycopg
import streamlit as st

MARTS_SCHEMA = f"{os.environ.get('DBT_SCHEMA', 'analytics')}_marts"


def _marts_conn() -> psycopg.Connection:
    """Open a read-only connection to the marts schema (the `marts_reader` role)."""
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "analytics"),
        user=os.environ.get("MARTS_READER_USER", "marts_reader"),
        password=os.environ.get("MARTS_READER_PASSWORD", "marts_reader"),
    )


@st.cache_data(ttl=300)
def load_mart(table: str) -> pd.DataFrame:
    """Read one marts table into a DataFrame (read-only role, marts schema only)."""
    with _marts_conn() as conn:
        return pd.read_sql(f'select * from "{MARTS_SCHEMA}"."{table}"', conn)


def main() -> None:
    st.set_page_config(page_title="Biomedical RAG Bench — Analytics - soon to deprecate", layout="wide")
    st.title("Biomedical RAG Bench — Retriever Analytics")
    st.caption(
        "Generator fixed per run · ground truth = graph traversal · "
        "**the compared variable is the retriever**."
    )

    fct = load_mart("fct_scored_answer")
    dim_ret = load_mart("dim_retriever_cond")
    dim_q = load_mart("dim_question")

    # Join the slicing dimensions onto the fact for display.
    df = (
        fct.merge(dim_ret, on="retriever_cond_sk", how="left", suffixes=("", "_ret"))
        .merge(
            dim_q[["question_sk", "type_id", "question_hop_count"]],
            on="question_sk",
            how="left",
        )
    )

    # ── headline metrics ──
    judged = df[df["is_judged"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scored answers", f"{len(df):,}")
    c2.metric("Pass rate (judged)", f"{judged['is_passed'].mean():.1%}" if len(judged) else "—")
    c3.metric("Total cost (USD)", f"${df['total_cost_usd'].sum():,.4f}")
    c4.metric("Error rate", f"{df['is_error'].mean():.1%}")

    st.divider()

    # ── pass rate + cost by retriever condition ──
    st.subheader("Outcome & cost by retriever condition")
    by_ret = (
        df.groupby("display_label")
        .agg(
            n=("scored_answer_sk", "count"),
            pass_rate=("is_passed", "mean"),
            avg_cost_usd=("total_cost_usd", "mean"),
            avg_latency_ms=("total_latency_ms", "mean"),
            avg_total_tokens=("generator_total_tokens", "mean"),
        )
        .sort_values("pass_rate", ascending=False)
    )
    st.dataframe(by_ret.style.format({
        "pass_rate": "{:.1%}", "avg_cost_usd": "${:.5f}",
        "avg_latency_ms": "{:.0f}", "avg_total_tokens": "{:.0f}",
    }), use_container_width=True)

    col_a, col_b = st.columns(2)
    col_a.caption("Pass rate by retriever")
    col_a.bar_chart(by_ret["pass_rate"])
    col_b.caption("Avg cost (USD) by retriever")
    col_b.bar_chart(by_ret["avg_cost_usd"])

    st.divider()

    # ── pass rate by question difficulty (question_hop_count) × retriever ──
    st.subheader("Pass rate by question hop-count × retriever")
    pivot = (
        df.dropna(subset=["question_hop_count"])
        .pivot_table(
            index="question_hop_count",
            columns="display_label",
            values="is_passed",
            aggfunc="mean",
        )
    )
    st.dataframe(pivot.style.format("{:.0%}", na_rep="—"), use_container_width=True)

    st.divider()

    # ── pricing reference (provenance for the cost column above) ──
    dim_pricing = load_mart("dim_token_pricing")
    srcs = ", ".join(sorted(dim_pricing["pricing_source"].dropna().unique())) or "—"
    st.subheader("Model token pricing (reference)")
    st.caption(
        f"USD per 1M tokens · source: **{srcs}**. The rates behind the cost column above; "
        "cost is computed in dbt, not in the dashboard."
    )
    st.dataframe(
        dim_pricing.sort_values(["provider", "model_resolved"])[
            ["provider", "model_resolved", "input_usd_per_mtok", "output_usd_per_mtok", "pricing_source"]
        ].rename(columns={
            "provider": "Provider", "model_resolved": "Model",
            "input_usd_per_mtok": "Input $/Mtok", "output_usd_per_mtok": "Output $/Mtok",
            "pricing_source": "Source",
        }),
        use_container_width=True, hide_index=True,
    )


if __name__ == "__main__":
    main()
