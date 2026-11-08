# Scan boxes with a handheld phone

The phone uses a **location marker**, then a **box QR**. The marker assigns a known
floor position to the box; this does not calculate an exact physical position from
a moving phone camera. Boxes scanned at the same marker share the same map point.

## Start and pair

```bash
.venv/bin/python launch.py --phone
```

Open the desktop dashboard at http://127.0.0.1:8501 and choose **Connect phone**.
Connect the phone and computer to the same Wi-Fi, then scan the pairing QR using
the phone's normal camera and open its link. Keep the terminal running.

If using the alternative ports:

```bash
.venv/bin/python launch.py --phone --api-port 8010 --dashboard-port 8510
```

The desktop dashboard is then http://127.0.0.1:8510. Only the restricted phone
scanner listens on the network (port 8020); the application API and dashboard
remain on localhost. A new pairing token is generated at each launch. Re-pair
phones after restarting. `--phone-port` changes the scanner port, and
`--phone-ip 192.168.x.x` overrides the auto-detected computer address if necessary.

Phone mode disables synthetic camera events and uses `packtrack-phone.db` by
default. Set `PACKTRACK_DB_URL` to choose another database. Records persist across
restarts. Phone records default to a one-hour stale-sighting threshold; override
with `PACKTRACK_STALE_AFTER` if needed.

## Test it

1. In **Connect phone**, download the **location marker sheet**. Cut out or show
   one marker at a time; a photo containing multiple markers is rejected.
2. In **Test QR labels**, download the six example box labels.
3. On the phone page, tap **Take photo / scan QR** and photograph **ZONE-A**.
4. The page shows the selected location and its assigned x,y coordinates. Take
   another photo of a box QR, for example **TEST-001**. The dashboard receives a
   placed box at ZONE-A's coordinates.
5. Scan **ZONE-B**, then the same box, to record a move to the new marker location.
6. Once the box is loaded, scan **TRUCK-01**, then the box label. Its status becomes
   collected and its last floor position is preserved.

Location selection expires after two minutes and is bound to the phone session.
Always scan another location marker when moving. The backend trusts only IDs in
its location configuration; coordinates printed in arbitrary QR data are not used.
A photo may contain several box labels, all assigned to the selected location.
A photo containing a location marker only selects that location; photograph the box
labels separately. Failed scans can be retried using the same photo/event IDs.
Keep the page open to retain a pending retry; reload does not preserve the photo.
Collection is terminal in this prototype, so use a fresh box ID for a new cycle.

The included ZONE-A/B/C marker coordinates are **examples**, clearly labelled in
the phone page. Do not treat them as measurements of your floor.

## Configure measured locations

Copy `examples/phone-locations.demo.json` to `locations.json` and replace the IDs,
names and x,y coordinates with measured points in a shared floor coordinate system.
Set `example_coordinates` to false only when the values have been measured.

```json
{
  "example_coordinates": false,
  "coordinate_frame": "factory-floor",
  "locations": [
    {"location_id":"RACK-01","name":"Rack 1","role":"placement","x":2.5,"y":4.0},
    {"location_id":"TRUCK-01","name":"Loading bay","role":"collection"}
  ]
}
```

Start with `.venv/bin/python launch.py --phone --locations locations.json`.
The dashboard's downloads are generated from that exact configuration. Coordinates
represent a rack, slot or zone marker; they are not inferred box-centre measurements.
Place the physical marker at that location and have operators scan it before its boxes.

To generate printable files separately:

```bash
.venv/bin/python tools/make_location_labels.py --config locations.json --out my-markers
```

The location QR contains only `{"type":"packtrack_location","location_id":"RACK-01"}`.
The server resolves its configured role and coordinate. The dashboard's register
shows the marker ID and position method. Hover over a shared map point to see all
boxes assigned there.

## Photo capture and live video

**Take photo / scan QR** uses the phone's camera/file picker, including on the
local HTTP page. Choose the rear camera and keep the complete QR and white border
visible. A compatible desktop browser can upload a JPEG/PNG instead.

Continuous live video uses `getUserMedia`, which browsers restrict to secure
contexts. For an HTTPS scanner, supply a certificate and key already trusted by
your phone:

```bash
.venv/bin/python launch.py --phone --phone-cert server.crt --phone-key server.key
```

The certificate must cover the host/IP in the pairing link. The launcher does not
install trust certificates, change browser security settings, or create a public
tunnel. The **Start live camera** button is enabled only when the page has a secure
context and camera APIs. Photo capture remains the default quick setup.

The photo travels only to the computer running PackTrack and is decoded in memory;
raw photos are not saved. The local HTTP option is intended for a trusted Wi-Fi
network. Share pairing links only with operators. HTTPS is needed for encrypted
transport as well as continuous video. If a phone cannot open the link, check the
computer's LAN IP, firewall/local-network permission, and whether the Wi-Fi isolates
clients. Live camera permission must be granted by the phone user.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/verify_phone.py
```

The HTTP test uses temporary databases and ports 8774/8775. It checks pairing,
location assignment, dashboard counts, retry deduplication and truck collection.
Physical phone camera permissions/capture need a device check on your network.
