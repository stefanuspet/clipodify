"""Debug script: visualisasi deteksi wajah per frame.

Jalankan: python tools/debug_faces.py <video_path> [--out /tmp/debug_faces]
Output: frame-frame PNG dengan bbox wajah yang terdeteksi digambar di atasnya.
"""

import argparse, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np
import mediapipe as mp

SAMPLE_TIMES = [0, 5, 10, 20, 30, 40, 50, 51, 52, 53, 60, 70, 75]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default="/tmp/debug_faces")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    BaseOptions       = mp.tasks.BaseOptions
    FaceLandmarker    = mp.tasks.vision.FaceLandmarker
    FaceLandmarkerOpts = mp.tasks.vision.FaceLandmarkerOptions
    VisionRunningMode = mp.tasks.vision.RunningMode
    MPImage           = mp.Image
    MPImageFormat     = mp.ImageFormat

    model_path = str(Path(__file__).parent.parent / "tmp" / "mp_models" / "face_landmarker.task")

    opts = FaceLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=model_path, delegate=BaseOptions.Delegate.CPU),
        running_mode=VisionRunningMode.IMAGE,
        num_faces=6,
        min_face_detection_confidence=0.4,
        min_face_presence_confidence=0.4,
    )
    lm = FaceLandmarker.create_from_options(opts)

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Video: {vid_w}x{vid_h} @ {fps:.1f}fps")

    colors = [(0,255,0),(0,120,255),(255,0,120),(255,255,0),(0,255,255),(255,0,255)]

    for t in SAMPLE_TIMES:
        frame_idx = int(t * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            print(f"  t={t}s: frame tidak ada")
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mpi = MPImage(image_format=MPImageFormat.SRGB, data=rgb)
        result = lm.detect(mpi)

        vis = frame.copy()
        n_faces = len(result.face_landmarks) if result.face_landmarks else 0
        print(f"\nt={t}s: {n_faces} wajah terdeteksi")

        if result.face_landmarks:
            for fi, face_lms in enumerate(result.face_landmarks):
                xs = [lm.x * vid_w for lm in face_lms]
                ys = [lm.y * vid_h for lm in face_lms]
                x1, y1 = int(min(xs)), int(min(ys))
                x2, y2 = int(max(xs)), int(max(ys))
                cx, cy = (x1+x2)//2, (y1+y2)//2
                col = colors[fi % len(colors)]
                cv2.rectangle(vis, (x1,y1), (x2,y2), col, 2)
                cv2.putText(vis, f"Face {fi} ({x1},{y1}) {x2-x1}x{y2-y1}",
                            (x1, max(0, y1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
                cv2.circle(vis, (cx,cy), 4, col, -1)
                print(f"  Face {fi}: bbox=({x1},{y1},{x2-x1},{y2-y1}) center=({cx},{cy})")

        # Tambah label waktu
        cv2.putText(vis, f"t={t}s  {n_faces} faces", (5,20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)

        out_path = os.path.join(args.out, f"t{t:03d}s.png")
        cv2.imwrite(out_path, vis)
        print(f"  → {out_path}")

    cap.release()
    lm.close()
    print(f"\nSelesai. Lihat gambar di: {args.out}")

if __name__ == "__main__":
    main()
