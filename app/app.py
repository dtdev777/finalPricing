"""finalPricing dashboard. All data comes from the FastAPI service."""
import os
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

import ui
from api_client import ApiError, get_json, post_json

OWN_STORE = os.getenv("OWN_STORE", "FPT Shop")
REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "60"))

EXAMPLES = [
    "Where is iPhone 16 128GB cheapest?",
    f"Where is {OWN_STORE} above the market?",
    "Which prices dropped this week?",
    "Latest price alerts",
]
HISTORY_RANGES = {"30 days": 30, "90 days": 90, "1 year": 365}

st.set_page_config(page_title="iPhone Price Intelligence", page_icon=":material/monitoring:", layout="wide")


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def load(path: str, **params: Any) -> Any:
    return get_json(path, **params)


def load_matrix() -> pd.DataFrame:
    return ui.price_matrix(load("/api/v1/products", limit=1000), OWN_STORE)


def empty_state() -> None:
    with st.container(border=True):
        st.markdown("#### :material/inventory_2: No prices yet")
        st.write("The dashboard fills in after the first pipeline run loads prices into PostgreSQL.")
        st.code("docker compose --profile jobs run --rm pipeline", language="powershell")


# ---------------------------------------------------------------- header + KPIs


@st.fragment(run_every=REFRESH_SECONDS)
def header() -> None:
    title, status = st.columns([3, 2], vertical_alignment="center")
    title.title("iPhone Price Intelligence", anchor=False)
    title.caption(f"Daily iPhone prices from Vietnamese retailers, seen from **{OWN_STORE}**.")

    try:
        kpis = load("/api/v1/kpis", store=OWN_STORE)
        position = ui.position_summary(load_matrix(), OWN_STORE)
    except ApiError as exc:
        with status.container(horizontal=True, horizontal_alignment="right"):
            st.badge("API offline", icon=":material/cloud_off:", color="red", help=str(exc))
        st.error("The API is not reachable. Check `docker compose ps`.", icon=":material/cloud_off:")
        return

    with status.container(horizontal=True, horizontal_alignment="right"):
        st.badge("API online", icon=":material/check_circle:", color="green")
        st.badge(f"Data {kpis['latest_crawl_date'] or 'pending'}", icon=":material/calendar_today:", color="blue")
        st.badge(f"Refresh {REFRESH_SECONDS}s", icon=":material/autorenew:", color="gray")

    index = position["price_index"]
    tracked = position["products"]
    cards = st.columns(4)
    cards[0].metric(
        "Price index",
        "-" if index is None else f"{index:.1f}",
        delta=None if index is None else f"{index - 100:+.1f} vs market",
        delta_color="inverse",
        help="Average of our price / market median price x 100. Below 100 means cheaper than the market.",
        icon=":material/speed:",
        border=True,
        height="stretch",
    )
    cards[1].metric(
        "Cheapest store on",
        f"{position['cheapest']} / {tracked}" if tracked else "-",
        help="Products where we match or beat the lowest price among tracked stores.",
        icon=":material/emoji_events:",
        border=True,
        height="stretch",
    )
    cards[2].metric(
        "Above market median",
        position["above_median"] if tracked else "-",
        help="Products we sell above the median price of the stores that carry them.",
        icon=":material/trending_up:",
        border=True,
        height="stretch",
    )
    cards[3].metric(
        "Alerts on latest day",
        kpis["alerts_latest_day"] if kpis["latest_crawl_date"] else "-",
        help=f"{kpis['stores_tracked']} stores tracked on the latest day.",
        icon=":material/notifications:",
        border=True,
        height="stretch",
    )


# ------------------------------------------------------------------ market tab


def gap_chart(matrix: pd.DataFrame) -> alt.Chart:
    data = matrix.dropna(subset=["own_vs_median_pct"]).assign(
        own_price=lambda df: df[OWN_STORE].map(ui.vnd),
        median_price=lambda df: df["market_median"].map(ui.vnd),
        cheapest=lambda df: df["cheapest_store"] + ", " + df["cheapest_price"].map(ui.vnd),
        gap=lambda df: df["own_vs_median_pct"].map("{:+.1f}%".format),
    )
    bars = (
        alt.Chart(data)
        .mark_bar(cornerRadiusEnd=4, height={"band": 0.7})
        .encode(
            x=alt.X(
                "own_vs_median_pct:Q",
                title=f"{OWN_STORE} price vs market median",
                axis=alt.Axis(labelExpr="format(datum.value, '+.1f') + '%'", tickCount=6),
            ),
            y=alt.Y(
                "product:N",
                sort=alt.EncodingSortField("own_vs_median_pct", order="descending"),
                title=None,
                axis=alt.Axis(labelLimit=240, labelOverlap=False, labelFontSize=12),
            ),
            color=alt.condition(alt.datum.own_vs_median_pct > 0, alt.value(ui.DEARER), alt.value(ui.CHEAPER)),
            tooltip=[
                alt.Tooltip("product:N", title="Product"),
                alt.Tooltip("gap:N", title="Vs median"),
                alt.Tooltip("own_price:N", title=OWN_STORE),
                alt.Tooltip("median_price:N", title="Market median"),
                alt.Tooltip("cheapest:N", title="Cheapest"),
            ],
        )
    )
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="#8a8a85").encode(x="x:Q")
    return (bars + zero).properties(height=max(160, 26 * len(data)))


def styled_matrix(matrix: pd.DataFrame) -> Any:
    stores = ui.store_columns(matrix, OWN_STORE)
    columns = ["product", *stores, "cheapest_store"]
    if OWN_STORE in stores:
        columns.append("own_vs_cheapest")
    view = matrix[columns].rename(
        columns={"product": "Product", "cheapest_store": "Cheapest at", "own_vs_cheapest": f"{OWN_STORE} gap"}
    )
    styler = (
        view.style.format(ui.vnd, subset=stores)
        .highlight_min(axis=1, subset=stores, props="background-color: rgba(27, 175, 122, 0.18); font-weight: 600")
    )
    if OWN_STORE in stores:
        gap = f"{OWN_STORE} gap"
        styler = styler.format(lambda value: "-" if pd.isna(value) else "Cheapest" if value <= 0 else ui.signed_vnd(value), subset=[gap])
        styler = styler.map(lambda value: "" if pd.isna(value) else "color: #d03b3b" if value > 0 else "color: #0ca30c", subset=[gap])
    return styler


@st.fragment(run_every=REFRESH_SECONDS)
def market_tab() -> None:
    try:
        matrix = load_matrix()
    except ApiError:
        return
    if matrix.empty:
        empty_state()
        return

    names = sorted(matrix["series"].unique(), key=ui.series_sort_key)
    filters = st.container(horizontal=True, vertical_alignment="bottom")
    picked = filters.pills("Series", names, selection_mode="multi", key="series_filter")
    behind_only = filters.toggle(f"Only where {OWN_STORE} is not cheapest", key="behind_only")

    shown = matrix[matrix["series"].isin(picked)] if picked else matrix
    if behind_only and "own_vs_cheapest" in shown:
        shown = shown[shown["own_vs_cheapest"] > 0]
    if shown.empty:
        st.info("No products match these filters.", icon=":material/filter_alt_off:")
        return

    if "own_vs_median_pct" in shown and shown["own_vs_median_pct"].notna().any():
        with st.container(border=True):
            st.markdown(f"**Where {OWN_STORE} stands**")
            st.caption(f"Red bars: {OWN_STORE} is dearer than the market median. Blue bars: cheaper.")
            st.altair_chart(gap_chart(shown), width="stretch")

    st.markdown("**Latest price by store**")
    st.caption("Green cells mark the lowest price for each product.")
    st.dataframe(
        styled_matrix(shown),
        hide_index=True,
        column_config={"Product": st.column_config.TextColumn(pinned=True)},
        height=min(38 + 35 * len(shown), 640),
        placeholder="-",
    )


# ----------------------------------------------------------------- history tab


def history_chart(history: pd.DataFrame, forecast: pd.DataFrame) -> alt.LayerChart:
    stores = ui.store_order(list(history["store"].unique()), OWN_STORE)
    color = alt.Color(
        "store:N",
        scale=alt.Scale(domain=stores, range=ui.store_colors(stores)),
        sort=stores,
        legend=alt.Legend(title=None, orient="bottom"),
    )
    history = history.assign(price_text=history["price"].map(ui.vnd))
    base = alt.Chart(history).encode(
        x=alt.X("crawl_date:T", title=None, axis=alt.Axis(format="%d/%m")),
        y=alt.Y(
            "price:Q",
            title=None,
            scale=alt.Scale(zero=False),
            axis=alt.Axis(labelExpr="format(datum.value / 1e6, '.1f') + 'M'"),
        ),
        color=color,
    )
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["crawl_date"], empty=False)
    lines = base.mark_line(interpolate="step-after").encode(
        strokeWidth=alt.condition(alt.datum.store == OWN_STORE, alt.value(3), alt.value(1.5))
    )
    points = base.mark_point(filled=True, size=70).encode(
        opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
        tooltip=[
            alt.Tooltip("crawl_date:T", title="Date", format="%d/%m/%Y"),
            alt.Tooltip("store:N", title="Store"),
            alt.Tooltip("price_text:N", title="Price"),
        ],
    ).add_params(nearest)
    rule = alt.Chart(history).mark_rule(color="#8a8a85", strokeDash=[3, 3]).encode(x="crawl_date:T").transform_filter(nearest)
    layers = [lines, rule, points]

    if not forecast.empty:
        forecast = forecast.assign(crawl_date=pd.to_datetime(forecast["forecast_date"]))
        own = history[history["store"] == OWN_STORE]
        if not own.empty:
            # Start the forecast at the last observed price so the two lines join.
            last = own.loc[own["crawl_date"].idxmax()]
            anchor = {"crawl_date": last["crawl_date"], "predicted_price": last["price"]}
            anchor |= {"lower_bound": last["price"], "upper_bound": last["price"]}
            forecast = pd.concat([pd.DataFrame([anchor]), forecast], ignore_index=True)
        forecast = forecast.assign(
            price_text=forecast["predicted_price"].map(ui.vnd),
            range_text=forecast["lower_bound"].map(ui.vnd) + " - " + forecast["upper_bound"].map(ui.vnd),
        )
        own_color = ui.store_colors([OWN_STORE])[0]
        band = alt.Chart(forecast).mark_area(opacity=0.15, color=own_color).encode(
            x="crawl_date:T", y=alt.Y("lower_bound:Q", title=None), y2="upper_bound:Q"
        )
        line = alt.Chart(forecast).mark_line(strokeDash=[6, 4], strokeWidth=2, color=own_color).encode(
            x="crawl_date:T",
            y=alt.Y("predicted_price:Q", title=None),
            tooltip=[
                alt.Tooltip("crawl_date:T", title="Forecast date", format="%d/%m/%Y"),
                alt.Tooltip("price_text:N", title="Predicted"),
                alt.Tooltip("range_text:N", title="Range"),
            ],
        )
        layers = [band, line, *layers]
    return alt.layer(*layers).properties(height=380)


@st.fragment(run_every=REFRESH_SECONDS)
def history_tab() -> None:
    try:
        matrix = load_matrix()
    except ApiError:
        return
    if matrix.empty:
        empty_state()
        return

    controls = st.container(horizontal=True, vertical_alignment="bottom")
    keys = list(matrix["product_match_key"])
    key = controls.selectbox("Product", keys, format_func=ui.product_label, key="history_product", width=320)
    span = controls.segmented_control("Range", list(HISTORY_RANGES), default="90 days", key="history_range", required=True)

    try:
        history = pd.DataFrame(load(f"/api/v1/products/{key}/history", days=HISTORY_RANGES[span]))
        forecast = pd.DataFrame(load(f"/api/v1/forecasts/{key}", store=OWN_STORE))
    except ApiError as exc:
        st.error(f"API unavailable: {exc}")
        return
    if history.empty:
        st.info("No history for this product yet.")
        return
    history["crawl_date"] = pd.to_datetime(history["crawl_date"])
    if not forecast.empty:
        forecast = forecast[forecast["model_name"] == forecast["model_name"].iloc[0]]

    latest = history[history["crawl_date"] == history["crawl_date"].max()]
    cheapest = latest.loc[latest["price"].idxmin()]
    own = history[history["store"] == OWN_STORE].sort_values("crawl_date")
    card = {"border": True, "height": "stretch"}
    cards = st.columns(3)
    if own.empty:
        cards[0].metric(OWN_STORE, "Not listed", **card)
    else:
        change = own["price"].iloc[-1] - own["price"].iloc[0]
        cards[0].metric(
            f"{OWN_STORE} now",
            ui.vnd(own["price"].iloc[-1]),
            delta=f"{ui.signed_vnd(change)} in {span}" if change else f"unchanged in {span}",
            delta_color="inverse",
            **card,
        )
    cards[1].metric("Cheapest now", ui.vnd(cheapest["price"]), delta=cheapest["store"], delta_color="off", delta_arrow="off", **card)
    if forecast.empty:
        cards[2].metric("Forecast", "Not available", help="Forecasts appear after the modelling stage runs.", **card)
    else:
        last = forecast.iloc[-1]
        cards[2].metric(
            f"Forecast {pd.to_datetime(last['forecast_date']):%d/%m}",
            ui.vnd(last["predicted_price"]),
            delta=last["model_name"],
            delta_color="off",
            delta_arrow="off",
            **card,
        )

    with st.container(border=True):
        st.altair_chart(history_chart(history, forecast), width="stretch")
        if not forecast.empty:
            st.caption(f"Dashed line and shaded band: {OWN_STORE} forecast and its range.")


# ------------------------------------------------------------------ alerts tab


@st.fragment(run_every=REFRESH_SECONDS)
def alerts_tab() -> None:
    level = st.segmented_control(
        "Severity", ["All", "High", "Medium", "Low"], default="All", key="alert_level", required=True
    )
    try:
        alerts = load("/api/v1/alerts", limit=100, **({} if level == "All" else {"severity": level.lower()}))
    except ApiError as exc:
        st.error(f"API unavailable: {exc}")
        return
    if not alerts:
        st.success("No alerts. Prices are moving within normal ranges.", icon=":material/check_circle:")
        return

    for alert in alerts:
        color, icon = ui.SEVERITY.get(alert["severity"], ui.SEVERITY["low"])
        with st.container(border=True, gap=None):
            row = st.container(horizontal=True, vertical_alignment="center")
            row.badge(alert["severity"].capitalize(), icon=icon, color=color, width=90)
            row.markdown(f"**{ui.product_label(alert['product_match_key'])}** at {alert['store']}", width=330)
            if alert["old_price"] and alert["new_price"]:
                change = alert["change_pct"]
                arrow = ":material/arrow_downward:" if alert["new_price"] < alert["old_price"] else ":material/arrow_upward:"
                row.markdown(
                    f"{ui.vnd(alert['old_price'])} {arrow} **{ui.vnd(alert['new_price'])}**"
                    + (f" ({change:+.1f}%)" if change is not None else ""),
                    width=330,
                )
            if alert["suspected_data_error"]:
                row.badge("Check data", icon=":material/report:", color="yellow", help="The change is large enough to be a crawl error.")
            row.space("stretch")
            row.caption(f"{alert['alert_type'].replace('_', ' ').capitalize()} | {alert['crawl_date']}", width="content")
            if alert["reason"]:
                st.caption(alert["reason"])


# --------------------------------------------------------------- assistant tab


def use_example() -> None:
    st.session_state.pending_question = st.session_state.example
    st.session_state.example = None


def show_message(message: dict[str, Any]) -> None:
    avatar = ":material/person:" if message["role"] == "user" else ":material/smart_toy:"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])
        if message.get("engine"):
            llm = message["engine"] == "llm"
            st.badge(
                "LLM router" if llm else "Rules",
                icon=":material/auto_awesome:" if llm else ":material/rule:",
                color="violet" if llm else "gray",
            )


@st.fragment
def assistant_tab() -> None:
    history = st.session_state.setdefault("chat", [])
    st.pills("Try asking", EXAMPLES, key="example", on_change=use_example)
    messages = st.container(height=440, border=True)
    typed = st.chat_input("Ask about iPhone prices, in English or Vietnamese")
    question = typed or st.session_state.pop("pending_question", None)

    with messages:
        if not history and not question:
            st.caption(
                ":material/forum: Ask which store is cheapest, how prices moved, "
                f"where {OWN_STORE} is above the market, forecasts or alerts."
            )
        for message in history:
            show_message(message)
        if question:
            asked = {"role": "user", "content": question}
            history.append(asked)
            show_message(asked)
            with st.spinner("Checking prices..."):
                try:
                    reply = post_json("/api/v1/assistant", {"question": question})
                    answer = {"role": "assistant", "content": reply["answer"], "engine": reply["engine"]}
                except ApiError as exc:
                    answer = {"role": "assistant", "content": f"Assistant unavailable: {exc}"}
            history.append(answer)
            show_message(answer)


header()
market, trend, alerts, assistant = st.tabs(
    [":material/table_chart: Market", ":material/show_chart: Price history", ":material/notifications: Alerts", ":material/smart_toy: Assistant"]
)
with market:
    market_tab()
with trend:
    history_tab()
with alerts:
    alerts_tab()
with assistant:
    assistant_tab()
