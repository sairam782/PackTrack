"""Decoding has to survive both backends and a real encoded frame."""

import io
import json

import cv2
import numpy as np
import pytest
import segno

import decoder


def qr(payload: str, *, scale: int = 8, border: int = 3) -> np.ndarray:
    buf = io.BytesIO()
    # make_qr, never make: segno would choose a Micro QR for short payloads,
    # which the OpenCV backend cannot read.
    segno.make_qr(payload, error="m").save(buf, kind="png", scale=scale, border=border)
    return cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)


def test_backend_is_one_we_know_about():
    assert decoder.BACKEND in {"pyzbar", "opencv"}


def test_json_payload_yields_supplier_and_part():
    img = qr(json.dumps({"box_id": "BOX-1", "supplier": "Acme", "part": "bracket"}))
    (found,) = decoder.decode_region(img)
    assert (found.box_id, found.supplier, found.part) == ("BOX-1", "Acme", "bracket")


def test_a_bare_id_is_accepted_as_the_box_id():
    (found,) = decoder.decode_region(qr("BOX-9"))
    assert found.box_id == "BOX-9"
    assert found.supplier is None


def test_json_without_a_box_id_is_treated_as_opaque():
    raw = json.dumps({"something": "else"})
    (found,) = decoder.decode_region(qr(raw))
    assert found.box_id == raw


def test_several_labels_in_one_frame():
    frame = np.full((600, 1000, 3), 210, np.uint8)
    for i, box_id in enumerate(("BOX-A1", "BOX-B2")):
        code = qr(box_id, scale=6)
        h, w = code.shape[:2]
        frame[120 : 120 + h, 80 + i * 460 : 80 + i * 460 + w] = code
    assert {d.box_id for d in decoder.decode_region(frame)} == {"BOX-A1", "BOX-B2"}


@pytest.mark.parametrize(
    "image",
    [None, np.zeros((0, 0, 3), np.uint8), np.zeros((40, 40, 3), np.uint8)],
)
def test_nothing_to_decode_is_not_an_error(image):
    assert decoder.decode_region(image) == []


def test_a_duplicate_label_in_one_frame_is_reported_once():
    frame = np.full((500, 900, 3), 210, np.uint8)
    code = qr("BOX-DUP", scale=6)
    h, w = code.shape[:2]
    frame[100 : 100 + h, 60 : 60 + w] = code
    frame[100 : 100 + h, 60 + w + 40 : 60 + w + 40 + w] = code
    assert [d.box_id for d in decoder.decode_region(frame)] == ["BOX-DUP"]
