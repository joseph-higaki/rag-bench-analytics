"""Streamlit dashboard — reads marts Parquet from object storage ONLY (CLAUDE.md rule #2).

Never connects to the warehouse. In local dev it reads from MinIO; in cloud, from the
same Parquet exported to S3 (Streamlit Community Cloud, free). The analytic story: the
generator is fixed per run, ground truth is graph traversal — so the compared variable
is the RETRIEVER. Every view slices outcomes/cost/latency by retriever condition.
"""

from __future__ import annotations

import io
import os

import boto3
import pandas as pd
import streamlit as st


@st.cache_data(ttl=300)
def load_mart(table: str) -> pd.DataFrame:
    """Download one marts Parquet object and return it as a DataFrame."""
    endpoint = os.environ.get("S3_ENDPOINT_URL") or None
    bucket = os.environ.get("S3_MARTS_BUCKET", "rag-bench-marts")
    prefix = os.environ.get("S3_MARTS_PREFIX", "marts/").rstrip("/")
    region = os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name=region)
    obj = s3.get_object(Bucket=bucket, Key=f"{prefix}/{table}.parquet")
    return pd.read_parquet(io.BytesIO(obj["Body"].read()))


def main() -> None:
    st.set_page_config(page_title="Biomedical RAG Bench — Analytics", layout="wide")
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
        .merge(dim_q[["question_sk", "type_id", "hop_count"]], on="question_sk", how="left")
    )

    # ── headline metrics ──
    judged = df[df["judged"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scored answers", f"{len(df):,}")
    c2.metric("Pass rate (judged)", f"{judged['is_pass'].mean():.1%}" if len(judged) else "—")
    c3.metric("Total cost (USD)", f"${df['total_cost_usd'].sum():,.4f}")
    c4.metric("Error rate", f"{df['is_error'].mean():.1%}")

    st.divider()

    # ── pass rate + cost by retriever condition ──
    st.subheader("Outcome & cost by retriever condition")
    by_ret = (
        df.groupby("display_label")
        .agg(
            n=("scored_answer_sk", "count"),
            pass_rate=("is_pass", "mean"),
            avg_cost_usd=("total_cost_usd", "mean"),
            avg_latency_ms=("total_latency_ms", "mean"),
            avg_total_tokens=("total_tokens", "mean"),
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

    # ── pass rate by question difficulty (hop_count) × retriever ──
    st.subheader("Pass rate by question hop-count × retriever")
    pivot = (
        df.dropna(subset=["hop_count"])
        .pivot_table(index="hop_count", columns="display_label", values="is_pass", aggfunc="mean")
    )
    st.dataframe(pivot.style.format("{:.0%}", na_rep="—"), use_container_width=True)


if __name__ == "__main__":
    main()
