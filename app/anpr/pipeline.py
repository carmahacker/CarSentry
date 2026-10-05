import re
from collections import Counter
from pathlib import Path
import cv2

from app.db.database import SessionLocal
from app.db.models import AnprSettings
from app.vision import anpr as core

PLATE_RE = re.compile(r"^[ABCEHKMOPTXY]\d{3}[ABCEHKMOPTXY]{2}\d{2,3}$")

_detector = None

def detector():
    global _detector
    if _detector is None:
        _detector = core.ANPRDetector()
    return _detector

def recognize(frames):
    with SessionLocal() as db:
        s = db.get(AnprSettings, 1)
        confidence = float(s.confidence if s else 0.35)
        vote_frames = max(1, int(s.vote_frames if s else 3))

    core.ANPR_CONFIDENCE = confidence
    d = detector()
    all_hits = []
    best = None
    for frame in frames[:vote_frames]:
        try:
            hits = d.detect(frame)
        except Exception:
            continue
        for hit in hits:
            text = (hit.get("text") or "").upper()
            if not text or not PLATE_RE.match(text):
                continue
            hit = dict(hit)
            hit["frame"] = frame
            all_hits.append(hit)
            score = float(hit.get("confidence", 0)) * 0.55 + float(hit.get("ocr_confidence", 0)) * 0.45
            if best is None or score > best[0]:
                best = (score, hit)

    if not all_hits:
        return None

    counts = Counter(h["text"] for h in all_hits)
    plate = counts.most_common(1)[0][0]
    candidates = [h for h in all_hits if h["text"] == plate]
    best = max(candidates, key=lambda h: float(h.get("confidence", 0))*0.55 + float(h.get("ocr_confidence", 0))*0.45)
    return {
        "plate": plate,
        "votes": counts[plate],
        "total": len(all_hits),
        "detector_confidence": float(best.get("confidence", 0)),
        "ocr_confidence": float(best.get("ocr_confidence", 0)),
        "box": best.get("box"),
        "frame": best["frame"],
    }

def annotate(frame, result):
    out = frame.copy()
    box = result.get("box")
    if box:
        x1, y1, x2, y2 = [int(v) for v in box]
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = result["plate"]
        cv2.rectangle(out, (x1, max(0, y1-30)), (x1+min(700, max(180, len(label)*13)), y1), (0,255,0), -1)
        cv2.putText(out, label, (x1+5, max(20, y1-8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0,0,0), 2, cv2.LINE_AA)
    return out
