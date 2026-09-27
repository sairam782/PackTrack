import pandas as pd
import requests
import streamlit as st

from config import cfg

st.set_page_config(page_title="PackTrack", layout="wide")
st.title("PackTrack — Factory Box Tracker")
st.caption(
    "Legacy view. The main dashboard, with video ingest and live crossings, "
    "is served by the API itself at http://127.0.0.1:8000/"
)
 

@st.cache_data(ttl=5)
def _fetch(path: str, **params):
    r = requests.get(f"{cfg.api_base_url}{path}", params=params, timeout=5)
    r.raise_for_status()
    return r.json()


try:
    stats = _fetch("/api/dashboard")
except requests.RequestException as e:
    st.error(f"API unreachable at {cfg.api_base_url}: {e}")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Total boxes", stats["kpis"]["total_boxes"])
c2.metric("On the floor", stats["kpis"]["in_stock"])
c3.metric("Departed", stats["kpis"]["departed"])

left, right = st.columns(2)
with left:
    st.subheader("By station")
    st.dataframe(pd.DataFrame(stats["stations"]), hide_index=True)
with right:
    st.subheader("By supplier")
    if stats["by_supplier"]:
        st.dataframe(pd.DataFrame(stats["by_supplier"]), hide_index=True)
    else:
        st.info("No suppliers tracked yet.")

st.subheader("Boxes")
status_filter = st.selectbox("State", ["all", "in_stock", "departed"])
params = {} if status_filter == "all" else {"state": status_filter}
boxes = _fetch("/api/boxes", **params)
if boxes:
    df = pd.DataFrame(boxes)
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.subheader("Mark collected")
    ids = [b["box_id"] for b in boxes if b["status"] != "collected"]
    if ids:
        pick = st.selectbox("Box to mark collected", ids)
        if st.button("Mark collected"):
            r = requests.patch(
                f"{cfg.api_base_url}/api/boxes/{pick}",
                json={"status": "collected"},
                timeout=5,
            )
            r.raise_for_status()
            st.cache_data.clear()
            st.rerun()
else:
    st.info("No boxes tracked yet.")
