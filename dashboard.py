import pandas as pd
import requests
import streamlit as st

from config import cfg

st.set_page_config(page_title="PackTrack", layout="wide")
st.title("PackTrack — Factory Box Tracker")
 

@st.cache_data(ttl=5)
def _fetch(path: str, **params):
    r = requests.get(f"{cfg.api_base_url}{path}", params=params, timeout=5)
    r.raise_for_status()
    return r.json()


try:
    stats = _fetch("/dashboard")
except requests.RequestException as e:
    st.error(f"API unreachable at {cfg.api_base_url}: {e}")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Total boxes", stats["total_boxes"])
c2.metric("Empty", stats["by_status"].get("empty", 0))
c3.metric("Active", stats["by_status"].get("active", 0))

left, right = st.columns(2)
with left:
    st.subheader("By station")
    st.bar_chart(pd.Series(stats["by_station"]))
with right:
    st.subheader("Supplier pickup queue")
    if stats["pickup_queue"]:
        st.dataframe(pd.DataFrame(stats["pickup_queue"]), hide_index=True)
    else:
        st.info("No empty boxes waiting for pickup.")

st.subheader("Boxes")
status_filter = st.selectbox("Status", ["all", "active", "empty", "collected"])
params = {} if status_filter == "all" else {"status": status_filter}
boxes = _fetch("/boxes", **params)
if boxes:
    df = pd.DataFrame(boxes)
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.subheader("Mark collected")
    ids = [b["box_id"] for b in boxes if b["status"] != "collected"]
    if ids:
        pick = st.selectbox("Box to mark collected", ids)
        if st.button("Mark collected"):
            r = requests.patch(
                f"{cfg.api_base_url}/boxes/{pick}",
                json={"status": "collected"},
                timeout=5,
            )
            r.raise_for_status()
            st.cache_data.clear()
            st.rerun()
else:
    st.info("No boxes tracked yet.")
