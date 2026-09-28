'use strict';
const $ = id => document.getElementById(id);
const hash = new URLSearchParams(location.hash.slice(1));
if (hash.get('token')) {
  sessionStorage.setItem('packtrackToken', hash.get('token'));
  history.replaceState(null, '', location.pathname);
}
const token = sessionStorage.getItem('packtrackToken') || '';
function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const s = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;
}
let device = sessionStorage.getItem('packtrackDevice');
if (!device) { device = uuid(); sessionStorage.setItem('packtrackDevice', device); }
let zoneToken = null, expiry = 0, busy = false, stream = null, liveTimer = null, pending = null, paired = false;
const report = new Map();
function message(text, error=false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; }
function clearLocation() {
  zoneToken = null; expiry = 0; $('locationName').textContent = 'Scan a location marker';
  $('locationDetails').textContent = 'Use a storage-zone marker or the truck loading marker.';
  $('countdown').textContent = ''; $('clearLocation').hidden = true;
  $('instruction').textContent = 'Take a photo of the location marker first. Then photograph the box QR codes at that location.';
}
$('clearLocation').onclick = clearLocation;
setInterval(() => {
  if (expiry && Date.now() >= expiry) { clearLocation(); message('Location expired. Scan the marker again before scanning more boxes.'); }
  if (expiry) $('countdown').textContent = `Location selection expires in ${Math.ceil((expiry-Date.now())/1000)}s`;
}, 1000);
async function request(path, body) {
  const response = await fetch(path, {method: body ? 'POST' : 'GET',
    headers: {'X-PackTrack-Token': token, ...(body ? {'Content-Type':'application/json'} : {})},
    body: body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Scan rejected. Check the phone clock and photo format.');
  return data;
}
function showResults(rows) {
  for (const row of rows) report.set(row.box_id, row);
  $('results').replaceChildren();
  for (const row of [...report.values()].reverse().slice(0,30)) {
    const item = document.createElement('div'); item.className = 'result';
    const title = document.createElement('strong'); title.textContent = row.box_id; item.append(title);
    const info = document.createElement('div'); info.className = 'muted';
    if (!row.saved) info.textContent = row.message;
    else if (!row.applied) info.textContent = `No state change: ${row.reason.replaceAll('_', ' ')}. Current status: ${row.status}.`;
    else info.textContent = row.status === 'collected' ? `Collected · ${row.location_id}` : `Placed · ${row.location_id} · (${row.x}, ${row.y}) m`;
    item.append(info); $('results').append(item);
  }
}
async function send(body) {
  if (busy) return;
  busy = true; $('retryButton').disabled = true; $('photoInput').disabled = true;
  message('Reading QR codes…');
  try {
    const data = await request('/phone/scan', body);
    pending = null; $('retryButton').hidden = true;
    if (data.kind === 'location') {
      zoneToken = data.location_token; expiry = Date.now() + data.expires_in * 1000;
      $('locationName').textContent = data.location.name || data.location.location_id;
      $('locationDetails').textContent = data.location.role === 'collection'
        ? 'Collection location · Scan boxes only after loading them into the truck.'
        : `Assigned position: (${data.location.x}, ${data.location.y}) metres · ${data.coordinate_frame}`;
      $('instruction').textContent = data.location.role === 'collection'
        ? 'Scan each loaded box to mark it collected.' : 'Scan boxes at this location. Change the location whenever you move.';
      $('clearLocation').hidden = false;
    }
    if (data.results) {
      showResults(data.results);
      if (data.results.some(r => !r.saved)) { pending = body; $('retryButton').hidden = false; stopLive(); }
    }
    message(data.message);
  } catch (error) {
    pending = body; $('retryButton').hidden = false; stopLive(); message(error.message, true);
  } finally {
    busy = false; $('retryButton').disabled = false; $('photoInput').disabled = !paired;
  }
}
function capture(source, width, height) {
  const scale = Math.min(1, 1800 / Math.max(width, height));
  const canvas = $('canvas'); canvas.width = Math.round(width*scale); canvas.height = Math.round(height*scale);
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', .92);
}
function bodyFor(data) {
  return {request_id:uuid(), device_id:device, image:data.split(',')[1], location_token:zoneToken,
          captured_at:new Date().toISOString()};
}
$('photoInput').onchange = async event => {
  const file = event.target.files[0]; if (!file || busy || !paired) return;
  const url = URL.createObjectURL(file); const img = new Image();
  try {
    await new Promise((resolve,reject) => { img.onload=resolve; img.onerror=reject; img.src=url; });
    const data = capture(img, img.naturalWidth, img.naturalHeight);
    $('preview').src = data; $('preview').hidden = false;
    await send(bodyFor(data));
  } catch { message('Could not open this photo. Try a JPEG or PNG image.',true); }
  finally { URL.revokeObjectURL(url); event.target.value=''; }
};
$('retryButton').onclick = () => { if (pending) send(pending); };
function stopLive() {
  if (liveTimer) clearInterval(liveTimer); liveTimer=null;
  if (stream) stream.getTracks().forEach(track => track.stop()); stream=null;
  $('video').srcObject=null; $('video').hidden=true; $('stopButton').hidden=true; $('liveButton').hidden=false;
}
$('stopButton').onclick = stopLive;
$('liveButton').onclick = async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
    $('video').srcObject=stream; $('video').hidden=false; await $('video').play();
    $('stopButton').hidden=false; $('liveButton').hidden=true;
    liveTimer = setInterval(() => {
      const video=$('video');
      if (!busy && !pending && video.videoWidth) send(bodyFor(capture(video,video.videoWidth,video.videoHeight)));
    }, 1500);
  } catch { stopLive(); message('Camera unavailable or permission denied. You can still use Take photo / scan QR.',true); }
};
window.addEventListener('pagehide',stopLive);
(async () => {
  $('photoInput').disabled=true;
  const liveSupported = !!(window.isSecureContext && navigator.mediaDevices?.getUserMedia);
  $('liveButton').disabled=!liveSupported;
  $('cameraNote').textContent=liveSupported ? 'Use the rear camera and keep the QR steady.' : 'On this local HTTP link, use Take photo / scan QR. Continuous live video requires HTTPS.';
  try {
    const config = await request('/phone/config'); paired=true; $('photoInput').disabled=false;
    $('connection').textContent='Connected to PackTrack'; $('example').hidden=!config.example_coordinates;
    message('Ready. Scan a location marker first.');
  } catch(error) { $('connection').textContent='Not paired'; $('liveButton').disabled=true; message(error.message,true); }
})();
