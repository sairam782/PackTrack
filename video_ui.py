"""Streamlit video review, shared by the standalone app and dashboard page."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pandas as pd
import streamlit as st

from video_analysis import VideoError, export_report, review_video, sample_video


def render():
    st.set_page_config(page_title='PackTrack · Video review', page_icon='📦', layout='wide')
    st.markdown('''<style>.block-container{max-width:1440px;padding-top:2rem}
    [data-testid="stMetric"]{background:white;border:1px solid #dce7e9;border-radius:12px;padding:14px}
    h1,h2,h3{color:#173b44}</style>''', unsafe_allow_html=True)
    st.caption('PACKTRACK  /  VIDEO REVIEW')
    st.title('From a factory walkthrough to visible evidence')
    st.write('Upload a clip, find readable labels, and review what appears at each timestamp.')
    sample = Path(os.environ['PACKTRACK_SAMPLE_VIDEO']) if os.getenv('PACKTRACK_SAMPLE_VIDEO') else None
    with st.sidebar:
        st.header('Review settings')
        mode = st.radio('Analysis', ['Nova + barcode scan', 'Local barcode scan'])
        frame_count = st.select_slider('Frames for visual review', options=[4, 8, 12, 16, 20], value=12)
        interval = st.selectbox('Barcode scan interval', [0.5, 1.0, 2.0], index=1, format_func=lambda x: f'Every {x:g} second(s)')
        region = st.selectbox('AWS region', ['us-east-2', 'us-east-1', 'us-west-2'])
        configured = os.getenv('AWS_BEARER_TOKEN_BEDROCK', '')
        entered = st.text_input('Bedrock API key', type='password', help='Kept in this browser session; not written to a file.', key='video_api_key')
        key = entered.strip() or configured
        if configured:
            st.caption('A server key is configured. Enter a fresh key above if it expires.')
        st.caption('Nova sends selected still frames to AWS Bedrock. Local barcode scanning stays on this computer.')
        st.caption('Handheld footage does not measure floor x,y. Repeated part labels do not identify individual boxes.')

    uploaded = st.file_uploader('Drop your factory video here', type=['mp4', 'mov', 'm4v', 'avi', 'mkv', 'webm'],
                                help='Up to 200 MB, 10 minutes, and 4K. MP4/H.264 is recommended for browser playback.')
    source = None
    if uploaded is not None:
        source = uploaded.getvalue()
        name = Path(uploaded.name).name
    elif sample and sample.is_file():
        source = sample.read_bytes()
        name = sample.name
        st.caption('Your supplied example is loaded. Upload another clip above to replace it.')
    if source is None:
        st.info('Upload a video to start. You can run local barcode detection without an API key.')
        return
    if len(source) > 200 * 1024 * 1024:
        st.error('Please use a video smaller than 200 MB.')
        return
    signature = hashlib.sha256(source).hexdigest()
    if st.session_state.get('video_signature') != signature:
        st.session_state['video_signature'] = signature
        st.session_state.pop('video_result', None)
        st.session_state.pop('evidence_frame', None)
    preview, controls = st.columns([1, 1.3], gap='large')
    with preview:
        st.video(source, format='video/mp4')
        st.caption(name)
    with controls:
        st.subheader('Review this recording')
        st.write('Barcode decoding records the exact payload and its position in the image. Nova reads visible printed labels and describes the selected frames.')
        st.info('Results are a review report. They do not mark boxes placed or collected in the inventory.')
        if mode.startswith('Nova'):
            st.caption(f'{frame_count} frames · up to {(frame_count + 3) // 4} Nova requests · AWS usage charges apply')
        if st.button('Analyze video', type='primary', use_container_width=True):
            if mode.startswith('Nova') and not key:
                st.error('Enter your Bedrock key in the sidebar, or choose Local barcode scan.')
            else:
                bar = st.progress(0, text='Opening video')
                def progress(value, label):
                    bar.progress(value, text=label)
                try:
                    with tempfile.TemporaryDirectory(prefix='packtrack-video-') as folder:
                        path = Path(folder) / ('upload' + Path(name).suffix.lower())
                        path.write_bytes(source)
                        result = sample_video(path, frame_count, interval, progress)
                    result['source_name'] = name
                    if mode.startswith('Nova'):
                        result = review_video(result, key, region, progress)
                    st.session_state['video_result'] = result
                    st.session_state.pop('evidence_frame', None)
                except VideoError as exc:
                    st.error(str(exc))
                finally:
                    bar.empty()
    result = st.session_state.get('video_result')
    if not result:
        return
    st.divider()
    meta = result['metadata']
    reviews = {r['frame_id']: r for r in result['reviews']}
    timestamps = {f['frame_id']: f['time_seconds'] for f in result['frames']}
    labels = [{'Time (s)': timestamps[r['frame_id']], 'Label text': label['text'],
               'Model confidence': label['confidence'], 'Frame': r['frame_id']}
              for r in result['reviews'] for label in r['labels']]
    cols = st.columns(4)
    cols[0].metric('Clip length', f'{meta["duration_seconds"]:.1f}s')
    cols[1].metric('Frames scanned locally', meta['scanned_frames'])
    cols[2].metric('Distinct decoded IDs', len({s['box_id'] for s in result['barcodes']}))
    cols[3].metric('Frames reviewed by Nova', len(reviews))
    for error in result['errors']:
        st.warning(error + ' Local barcode results and completed frame reviews remain below.')
    if meta['skipped_frames']:
        st.warning(f'{meta["skipped_frames"]} sampled frames could not be decoded.')
    overview, evidence, codes, report = st.tabs(['Visual summary', 'Frame evidence', 'Decoded barcodes', 'Export report'])
    with overview:
        if result['summaries']:
            for summary in result['summaries']:
                st.write(summary)
            st.caption('AI observations need review. Counts are per image; the same container can appear in multiple frames.')
            counts = [{'Time (s)': timestamps[r['frame_id']], 'Visible containers (estimate)': r['visible_containers']}
                      for r in result['reviews'] if r['visible_containers'] is not None]
            if counts:
                st.bar_chart(pd.DataFrame(counts).set_index('Time (s)'), color='#117565')
            st.subheader('Printed label sightings')
            if labels:
                st.dataframe(pd.DataFrame(labels), hide_index=True, use_container_width=True)
                st.caption('These are model-transcribed labels, not verified unique box IDs. Confidence is self-reported by the model.')
            else:
                st.info('Nova did not report readable labels in the selected frames.')
        else:
            st.info('No Nova review yet. Use Nova + barcode scan to inspect printed labels and visible containers.')
    with evidence:
        selected = st.select_slider('Select a timestamp', options=[f['frame_id'] for f in result['frames']],
                                    format_func=lambda i: f'{timestamps[i]:.1f}s', key='evidence_frame')
        frame = next(f for f in result['frames'] if f['frame_id'] == selected)
        a, b = st.columns([1, 1.3])
        a.image(frame['jpeg'], caption=f'Frame {selected} · {frame["time_seconds"]:.1f}s', use_column_width=True)
        with b:
            reviewed = reviews.get(selected)
            if reviewed:
                st.subheader('What is visible')
                st.write(reviewed['activity'])
                st.write('Visible containers (estimate):', reviewed['visible_containers'] if reviewed['visible_containers'] is not None else 'Uncertain')
                if reviewed['limitations']:
                    st.caption(reviewed['limitations'])
                if reviewed['labels']:
                    st.dataframe(pd.DataFrame(reviewed['labels']), hide_index=True, use_container_width=True)
            else:
                st.caption('This frame has not been reviewed by Nova.')
            if st.checkbox('Play from selected timestamp'):
                st.video(source, start_time=int(frame['time_seconds']))
    with codes:
        if result['barcodes']:
            st.dataframe(pd.DataFrame(result['barcodes']), hide_index=True, use_container_width=True)
        else:
            st.info('No readable QR/barcodes were decoded in the sampled frames. Move closer, pause over labels, or try a shorter scan interval.')
        st.caption('Pixel x,y refers to the barcode centre in the video image. Physical floor coordinates and collection status are not inferred.')
    with report:
        st.download_button('Download review JSON', json.dumps(export_report(result), indent=2), 'packtrack-video-review.json', 'application/json')
        if labels:
            st.download_button('Download label sightings CSV', pd.DataFrame(labels).to_csv(index=False), 'packtrack-video-labels.csv', 'text/csv')
        if result['barcodes']:
            st.download_button('Download decoded barcodes CSV', pd.DataFrame(result['barcodes']).to_csv(index=False), 'packtrack-video-barcodes.csv', 'text/csv')
        st.caption('Reports include timestamps and uncertainty. Uploaded videos are processed in temporary files and are not added to the project.')
