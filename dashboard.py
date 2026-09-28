"""PackTrack: live floor positions and camera-confirmed collections."""
from datetime import datetime, timezone
import io
import os
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import qrcode
import streamlit as st

from config import cfg

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="PackTrack · Floor operations", page_icon="📦", layout="wide")
st.markdown('''<style>
.stApp {background:#f4f7fa;}
.block-container {padding-top:2rem; max-width:1500px;}
h1,h2,h3 {color:#173b44;}
[data-testid="stMetric"] {background:white;border:1px solid #dce7e9;border-radius:14px;padding:18px 22px;}
[data-testid="stMetricValue"] {color:#117565;}
[data-testid="stMetricLabel"] {color:#567079;}
.stButton > button[kind="primary"] {background:#117565;border-color:#117565;}
[data-testid="stTabs"] {margin-top:1rem;}
</style>''', unsafe_allow_html=True)
st.caption("FACTORY OPERATIONS  /  BOX VISIBILITY")
st.title("PackTrack")
st.write("Every box located. Every collection accounted for.")


def fetch(path):
    result = requests.get(f"{cfg.api_base_url}{path}", timeout=5)
    result.raise_for_status()
    return result.json()


def action(path, payload=None):
    try:
        result = requests.post(f"{cfg.api_base_url}{path}", json=payload or {}, timeout=10)
        result.raise_for_status()
    except requests.RequestException as exc:
        detail = "The tracking service could not complete this action. Please retry."
        if exc.response is not None:
            try:
                detail = exc.response.json().get("detail", detail)
            except ValueError:
                pass
        st.error(detail)
        return
    st.session_state["notice"] = "Simulation updated. The dashboard shows the recorded camera result."
    st.rerun()


def age(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def state(box):
    if box["status"] == "collected":
        return "Collected"
    return "Needs check" if box["is_stale"] else "On floor"


def register_rows(boxes):
    return [{"Box": b["box_id"], "Supplier": b["supplier_name"] or "Unknown", "Part": b["part_type"] or "—",
             "Status": state(b), "Floor camera": b["station_id"],
             "X (m)": b["floor_x"], "Y (m)": b["floor_y"], "Coordinate frame": b["coordinate_frame"] or "Uncalibrated",
             "Location marker": b.get("location_id") or "—",
             "Position method": "Assigned marker location" if b.get("position_source") == "location_marker" else "Camera calibration" if b["calibration_id"] else "Uncalibrated",
             "Last seen": age(b["last_seen"]), "Collected at (UTC)": b["collected_at"],
             "Truck camera": b["collection_camera_id"] or "—"} for b in boxes]


def draw_floor(boxes):
    frames = sorted({b["coordinate_frame"] for b in boxes if b["coordinate_frame"]})
    if not frames:
        st.info("No calibrated positions yet. Start a configured floor camera to see boxes here.")
        return
    c1, c2 = st.columns([3, 2])
    selected_frame = c1.selectbox("Floor coordinate frame", frames, key="floor_frame")
    show_collected = c2.checkbox("Show collected positions", value=False)
    points = [dict(box=b["box_id"], x=b["floor_x"], y=b["floor_y"], status=state(b),
                   supplier=b["supplier_name"] or "Unknown", camera=b["station_id"])
              for b in boxes if b["coordinate_frame"] == selected_frame and b["floor_x"] is not None
              and b["floor_y"] is not None and (show_collected or b["status"] != "collected")]
    if not points:
        st.success("This floor is clear. Enable collected positions to review the last known locations.")
        return
    # Multiple handheld scans can intentionally share the same marker point.
    grouped = {}
    for point in points:
        key = (point["x"], point["y"])
        grouped.setdefault(key, []).append(point)
    points = [dict(x=x, y=y, box=", ".join(p["box"] for p in members),
                   label=members[0]["box"] if len(members) == 1 else f"{len(members)} boxes",
                   supplier=", ".join(sorted({p["supplier"] for p in members})),
                   camera=", ".join(sorted({p["camera"] for p in members})),
                   status="Needs check" if any(p["status"] == "Needs check" for p in members)
                   else "On floor" if any(p["status"] == "On floor" for p in members) else "Collected")
              for (x, y), members in grouped.items()]
    # Stable metre bounds from all stored positions in this frame. Never
    # combine unrelated coordinate systems on one map.
    all_points = [b for b in boxes if b["coordinate_frame"] == selected_frame and b["floor_x"] is not None]
    xmin, xmax = min(0, min(b["floor_x"] for b in all_points)) - 1, max(b["floor_x"] for b in all_points) + 2
    ymin, ymax = min(0, min(b["floor_y"] for b in all_points)) - 1, max(b["floor_y"] for b in all_points) + 2
    width = 760
    height = max(280, min(600, round(width * (ymax - ymin) / (xmax - xmin))))
    # Expand the shorter axis to preserve scale even when chart height is capped.
    yspan = (xmax - xmin) * height / width
    if yspan >= ymax - ymin:
        ymax = ymin + yspan
    else:
        xmax = xmin + (ymax - ymin) * width / height
    spec = {
        "width": width, "height": height, "background": "#ffffff",
        "data": {"values": points},
        "encoding": {
            "x": {"field": "x", "type": "quantitative", "title": "Floor X (metres)", "scale": {"domain": [xmin, xmax], "nice": False}},
            "y": {"field": "y", "type": "quantitative", "title": "Floor Y (metres)", "scale": {"domain": [ymin, ymax], "nice": False}},
        },
        "layer": [
            {"mark": {"type": "point", "filled": True, "size": 260, "stroke": "white", "strokeWidth": 2},
             "encoding": {"color": {"field": "status", "type": "nominal", "title": None,
                         "scale": {"domain": ["On floor", "Needs check", "Collected"], "range": ["#0e9f83", "#e49b22", "#8fa2b1"]}},
                          "tooltip": [{"field": "box", "title": "Box"}, {"field": "supplier", "title": "Supplier"},
                                      {"field": "x", "format": ".2f", "title": "X (m)"}, {"field": "y", "format": ".2f", "title": "Y (m)"},
                                      {"field": "camera", "title": "Camera"}, {"field": "status", "title": "Status"}]}},
            {"mark": {"type": "text", "dy": -20, "fontSize": 12, "color": "#173b44"},
             "encoding": {"text": {"field": "label"}}},
        ],
        "config": {"view": {"stroke": "#e1e9ed"},
                   "axis": {"gridColor": "#edf2f5", "labelColor": "#375963", "titleColor": "#375963"},
                   "legend": {"orient": "bottom", "labelColor": "#375963"}},
    }
    st.vega_lite_chart(spec=spec, use_container_width=True)
    st.caption("Positions are in metres. Collected positions are historical. Amber points need a new camera sighting.")
    if any(b.get("position_source") == "location_marker" for b in boxes):
        st.caption("Phone scans use the selected marker’s coordinates. Boxes at the same marker share a point; hover for box IDs.")


@st.cache_data
def pairing_qr(url):
    output = io.BytesIO()
    qrcode.make(url).save(output, format="PNG")
    return output.getvalue()


@st.fragment(run_every="2s")
def workspace():
    try:
        stats, boxes = fetch("/dashboard"), fetch("/boxes")
    except requests.RequestException:
        st.error("Cannot reach the tracking service. Start PackTrack and retry.")
        st.code(".venv/bin/python launch.py")
        st.button("Retry connection")
        return
    if stats["demo_mode"]:
        st.info("Interactive demo · Synthetic camera scenes and floor calibration · Your real database is untouched.")
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    st.caption(f"Updated {datetime.now(timezone.utc):%H:%M:%S} UTC · Refreshes every 2 seconds")
    columns = st.columns(4)
    columns[0].metric("Boxes on floor", stats["present_boxes"])
    columns[1].metric("Collected today", stats["collected_today"])
    columns[2].metric("Needs location check", stats["stale_boxes"])
    columns[3].metric("Total registered", stats["total_boxes"])

    if stats["demo_mode"]:
        with st.expander("Try a truck pickup", expanded=True):
            st.caption("Simulate moving a labelled box into the truck. Its QR is decoded in the truck region before collection is recorded.")
            available = sorted(b["box_id"] for b in boxes if b["status"] != "collected")
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            selected = c1.selectbox("Box to load", available or ["All boxes collected"], disabled=not available, key="demo_box")
            if c2.button("Load into truck", type="primary", disabled=not available, use_container_width=True):
                action("/demo/collect", {"box_id": selected})
            auto_label = "Pause pickups" if stats["auto_collect"] else "Auto-play pickups"
            if c3.button(auto_label, use_container_width=True):
                action("/demo/automation", {"enabled": not stats["auto_collect"]})
            if c4.button("New batch", disabled=bool(available), use_container_width=True):
                action("/demo/batch")
            if stats["auto_collect"]:
                st.caption("Auto-play is on: the simulated truck collects one box every 12 seconds.")

    floor, inventory, collection, activity, labels, phone = st.tabs(["Floor overview", "Box register", "Collections", "Camera activity", "Test QR labels", "Connect phone"])
    with floor:
        st.subheader("Factory floor")
        draw_floor(boxes)
        left, right = st.columns(2)
        with left:
            st.subheader("Boxes by supplier")
            rows = [{"Supplier": s["supplier"], "State": name, "Boxes": s[key]}
                    for s in stats["supplier_summary"] for key, name in
                    (("on_floor", "On floor"), ("collected", "Collected"), ("stale", "Needs check"))]
            if rows:
                st.vega_lite_chart(pd.DataFrame(rows), {"mark": "bar", "height": 220,
                    "encoding": {"y": {"field": "Supplier", "type": "nominal", "title": None},
                                 "x": {"field": "Boxes", "type": "quantitative", "axis": {"tickMinStep": 1}},
                                 "color": {"field": "State", "type": "nominal", "scale": {"domain": ["On floor", "Collected", "Needs check"], "range": ["#0e9f83", "#8fa2b1", "#e49b22"]}}}}, use_container_width=True)
            else:
                st.caption("Supplier totals appear after the first scan.")
        with right:
            st.subheader("Floor cameras")
            st.dataframe(pd.DataFrame([{"Camera": k, "Recently seen boxes": v} for k, v in stats["by_station"].items()],
                                      columns=["Camera", "Recently seen boxes"]), hide_index=True, use_container_width=True)
            st.caption("Counts reflect recent box sightings, not camera connection health.")
        if stats["stale_boxes"]:
            st.warning(f"{stats['stale_boxes']} box(es) have no sighting within {stats['stale_after_seconds']:g}s. Verify their last known locations.")

    with inventory:
        st.subheader("Every box, one record")
        a, b, c = st.columns(3)
        supplier = a.selectbox("Supplier", ["All suppliers"] + sorted({x["supplier_name"] or "Unknown" for x in boxes}), key="supplier")
        status = b.selectbox("Status", ["All boxes", "On floor", "Collected", "Needs check"], key="status")
        search = c.text_input("Find a box", placeholder="Box ID or part name", key="search")
        filtered = [x for x in boxes if (supplier == "All suppliers" or (x["supplier_name"] or "Unknown") == supplier)
                    and (status == "All boxes" or state(x) == status)
                    and (not search or search.lower() in f"{x['box_id']} {x['part_type'] or ''}".lower())]
        rows = register_rows(filtered)
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.download_button("Export displayed boxes", pd.DataFrame(rows).to_csv(index=False), "packtrack-boxes.csv", "text/csv")
        else:
            st.info("No boxes match these filters.")
        st.caption("Status changes automatically from camera evidence. Floor positions remain available after collection.")
        selected_box = st.selectbox("Inspect a box’s camera history", ["Choose a box"] + sorted(x["box_id"] for x in filtered), key="inspect")
        if selected_box != "Choose a box":
            try:
                evidence = fetch(f"/boxes/{quote(selected_box, safe='')}/observations")
                st.dataframe(pd.DataFrame(evidence), hide_index=True, use_container_width=True)
            except requests.RequestException:
                st.warning("Camera history could not be loaded. It will retry on refresh.")

    with collection:
        st.subheader("Confirmed truck collections")
        st.caption("Collected today uses UTC. Only recorded collection events contribute to the hourly chart.")
        timeline = pd.DataFrame(stats["collection_timeline"])
        st.vega_lite_chart(timeline, {"mark": {"type": "bar", "color": "#117565"}, "height": 230,
            "encoding": {"x": {"field": "hour", "type": "temporal", "title": "Collection hour (UTC)", "scale": {"type": "utc"}},
                         "y": {"field": "boxes", "type": "quantitative", "title": "Boxes collected", "axis": {"tickMinStep": 1}},
                         "tooltip": [{"field": "hour", "type": "temporal"}, {"field": "boxes"}]}}, use_container_width=True)
        collected = [b for b in boxes if b["status"] == "collected"]
        if collected:
            st.dataframe(pd.DataFrame(register_rows(collected)), hide_index=True, use_container_width=True)
        else:
            st.info("No collections recorded yet. A confirmed truck-camera sighting will appear here automatically.")

    with activity:
        st.subheader("Recent camera observations")
        st.caption("An ignored observation is retained as evidence but does not change the box’s state.")
        try:
            events = fetch("/activity")
            if events:
                rows = [{"Time (UTC)": e["observed_at"], "Box": e["box_id"], "Camera": e["camera_id"],
                         "Event": e["event"].title(), "Result": "Recorded" if e["applied"] else e["reason"],
                         "X (m)": e["floor_x"], "Y (m)": e["floor_y"]} for e in events]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            else:
                st.info("Camera observations will appear here after the first scan.")
        except requests.RequestException:
            st.warning("Camera activity is temporarily unavailable.")

    with labels:
        st.subheader("Scan these labels")
        st.write("Print one unique QR per box. Present it to the floor camera, then the same label to the truck camera.")
        folder = ROOT / "assets/qr"
        if (folder / "test-labels.zip").exists():
            c1, c2 = st.columns(2)
            c1.download_button("Download all six QR labels", (folder / "test-labels.zip").read_bytes(), "packtrack-test-labels.zip", "application/zip")
            c2.download_button("Download printable sheet", (folder / "test-labels.png").read_bytes(), "packtrack-test-labels.png", "image/png")
            cols = st.columns(3)
            for index, path in enumerate(sorted(folder.glob("TEST-*.png"))):
                with cols[index % 3]:
                    st.image(str(path), caption=path.stem, width=220)
        else:
            st.code(".venv/bin/python tools/make_labels.py")
        st.caption("Real-camera testing requires your measured calibration and a truck-interior region. The demo uses generated video frames, not your webcam.")

    with phone:
        st.subheader("Scan from your phone")
        try:
            connection = fetch("/phone-connection")
        except requests.RequestException:
            connection = {"enabled": False}
        if not connection["enabled"]:
            st.write("Start phone mode, then connect the phone and this computer to the same Wi-Fi.")
            st.code(".venv/bin/python launch.py --phone")
        else:
            st.write("Open your phone’s camera and scan this pairing QR. Keep both devices on the same Wi-Fi.")
            st.image(pairing_qr(connection["url"]), width=240, caption="Pair with this PackTrack session")
            st.link_button("Open phone scanner", connection["url"])
            st.caption("The pairing link grants scan access for this run. Share it only with your operators.")
            st.markdown("1. Scan a **location marker**.\n2. Scan the **box QR labels** at that location.\n3. At pickup, scan the **truck marker**, then each loaded box.")
            st.write("The location marker supplies the assigned x,y coordinates. Scan another marker when you move; selections expire after two minutes.")
            folder = Path(os.getenv("PACKTRACK_PHONE_MARKER_DIR", str(ROOT / "assets/locations")))
            if (folder / "location-markers.png").exists():
                st.download_button("Download location marker sheet", (folder / "location-markers.png").read_bytes(), "packtrack-location-markers.png", "image/png")
                st.download_button("Download all location markers", (folder / "location-markers.zip").read_bytes(), "packtrack-location-markers.zip", "application/zip")
            st.caption("Take photo / scan QR works on the local HTTP page. Continuous live video requires HTTPS with a certificate trusted by the phone.")


workspace()
