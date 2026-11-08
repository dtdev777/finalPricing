"""finalPricing dashboard. All data comes from the FastAPI service."""
import os

import pandas as pd
import streamlit as st

from api_client import ApiError, get_json, post_json

OWN_STORE = os.getenv("OWN_STORE", "FPT Shop")
REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "60"))

st.set_page_config(page_title="finalPricing", layout="wide")
st.title("iPhone Price Intelligence")
st.caption(f"Reference retailer: {OWN_STORE}")


@st.fragment(run_every=REFRESH_SECONDS)
def render() -> None:
    try:
        kpis = get_json("/api/v1/kpis", store=OWN_STORE)
        alerts = get_json("/api/v1/alerts", limit=20)
        products = get_json("/api/v1/products", limit=200)
    except ApiError as exc:
        st.error(f"API unavailable: {exc}")
        return

    st.subheader("Alerts")
    if alerts:
        st.dataframe(pd.DataFrame(alerts), hide_index=True)
    else:
        st.info("No alerts yet.")

    columns = st.columns(4)
    columns[0].metric("Latest data", kpis["latest_crawl_date"] or "No data")
    columns[1].metric("Products tracked", kpis["products_tracked"])
    columns[2].metric("Stores tracked", kpis["stores_tracked"])
    columns[3].metric("Average price index", kpis["avg_price_index"] or "n/a")

    st.subheader("Latest prices")
    if products:
        st.dataframe(pd.DataFrame(products), hide_index=True)
    else:
        st.info("No products loaded yet. Run the pipeline to populate PostgreSQL.")


render()

st.subheader("AI Assistant")
st.caption("Ask about the cheapest store, store comparisons, forecasts or alerts. English or Vietnamese.")
history = st.session_state.setdefault("chat", [])
for message in history:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if question := st.chat_input("Where is iPhone 16 128GB cheapest?"):
    history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    try:
        reply = post_json("/api/v1/assistant", {"question": question})
        content = f"{reply['answer']}\n\n_Answered by: {reply['engine']}_"
    except ApiError as exc:
        content = f"Assistant unavailable: {exc}"
    history.append({"role": "assistant", "content": content})
    with st.chat_message("assistant"):
        st.markdown(content)
