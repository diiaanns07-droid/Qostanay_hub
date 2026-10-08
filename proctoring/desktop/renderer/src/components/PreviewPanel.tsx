// Operator preview. Frames come ONLY from bridge.subscribePreview (backend capture, A02). No getUserMedia.
// Overlays: normalized coordinates of the UNMIRRORED frame; when the preview is mirrored, x' = 1 − x.
// The image box keeps the frame aspect ratio, so overlays line up under letterboxing.
import { useEffect, useRef, useState } from "react";
import type { AttentionObservation, BBox, PhoneObservation, PreviewFrameMeta } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { useLive } from "../lib/liveStore";
import { num, sessionT } from "../lib/format";
import { SOURCE_MODE } from "../lib/labels";
import { Badge } from "./ui";

/** An older observation is drawn (dashed, labelled with its lag) only within this window; otherwise hidden. */
const LAG_DRAW_MS = 400;
const NO_FRAME_MS = 3000;

interface Frame {
  meta: PreviewFrameMeta;
  url: string;
  receivedAt: number;
}

export function PreviewPanel({ sessionId }: { sessionId: string }) {
  const { bridge, live } = useApp();
  useLive(live, "obs");
  const [frame, setFrame] = useState<Frame | null>(null);
  const [mirror, setMirror] = useState(false);
  const [now, setNow] = useState(Date.now());
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    const unsub = bridge.subscribePreview((meta, jpeg) => {
      if (meta.session_id !== sessionId) return;
      const url = URL.createObjectURL(new Blob([jpeg as BlobPart], { type: "image/jpeg" }));
      const prev = urlRef.current;
      urlRef.current = url;
      setFrame({ meta, url, receivedAt: Date.now() });
      if (prev) setTimeout(() => URL.revokeObjectURL(prev), 200);
    });
    return () => {
      unsub();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    };
  }, [bridge, sessionId]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const frozen = frame !== null && now - frame.receivedAt > NO_FRAME_MS;
  const meta = frame?.meta;
  // Pick the observation made on exactly the shown frame (frame_id); analyzers skip frames, so otherwise take
  // the nearest older one and draw it only if it is recent, dashed and labelled — never as if it were this frame.
  const pick = (kind: "attention" | "phone") => {
    if (!meta) return { obs: null, exact: false, lagMs: null as number | null, draw: false };
    const r = live.observationFor(kind, meta.frame_id, meta.t_session_ms);
    const draw = !!r.obs && (r.exact || (r.lagMs !== null && r.lagMs <= LAG_DRAW_MS));
    return { ...r, draw };
  };
  const a = pick("attention");
  const p = pick("phone");
  const att = a.obs as AttentionObservation | null;
  const phone = p.obs as PhoneObservation | null;
  const attRel = { draw: a.draw, sync: a.exact, age: a.lagMs };
  const phoneRel = { draw: p.draw, sync: p.exact, age: p.lagMs };

  const box = (b: BBox) => {
    const x = mirror ? 1 - b.x_max : b.x_min;
    return { x, y: b.y_min, w: b.x_max - b.x_min, h: b.y_max - b.y_min };
  };

  return (
    <div className="preview">
      <div className="preview-frame-wrap">
        {frame && meta ? (
          <div className="preview-box" style={{ aspectRatio: `${meta.width} / ${meta.height}` }}>
            <img src={frame.url} alt="Кадр источника (только для преподавателя)" className={mirror ? "mirrored" : ""} draggable={false} />
            <svg className="overlay" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
              {attRel.draw &&
                att?.faces.map((f, i) => {
                  const r = box(f.bbox);
                  return (
                    <rect
                      key={`f${i}`}
                      x={r.x}
                      y={r.y}
                      width={r.w}
                      height={r.h}
                      className={`ov ${f.is_primary ? "ov-face" : "ov-face2"} ${attRel.sync ? "" : "ov-lag"}`}
                      vectorEffect="non-scaling-stroke"
                    />
                  );
                })}
              {phoneRel.draw &&
                phone?.detections.map((d, i) => {
                  const r = box(d.bbox);
                  return (
                    <rect
                      key={`p${i}`}
                      x={r.x}
                      y={r.y}
                      width={r.w}
                      height={r.h}
                      className={`ov ov-phone ${phoneRel.sync ? "" : "ov-lag"}`}
                      vectorEffect="non-scaling-stroke"
                    />
                  );
                })}
            </svg>
            <div className="ov-labels">
              {attRel.draw &&
                att?.faces.map((f, i) => {
                  const r = box(f.bbox);
                  return (
                    <span key={`fl${i}`} className={`ov-tag ${f.is_primary ? "tag-face" : "tag-face2"}`} style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%` }}>
                      {f.is_primary ? "лицо" : "2-е лицо"}
                      {attRel.sync ? "" : ` · −${Math.round(attRel.age ?? 0)} мс`}
                    </span>
                  );
                })}
              {phoneRel.draw &&
                phone?.detections.map((d, i) => {
                  const r = box(d.bbox);
                  return (
                    <span key={`pl${i}`} className="ov-tag tag-phone" style={{ left: `${r.x * 100}%`, top: `${(r.y + r.h) * 100}%` }}>
                      телефон {num(d.confidence, 2)}
                      {phoneRel.sync ? "" : ` · −${Math.round(phoneRel.age ?? 0)} мс`}
                    </span>
                  );
                })}
            </div>
            <div className="preview-corner">
              <Badge tone={meta.source_mode === "live" ? "accent" : "warn"}>{SOURCE_MODE[meta.source_mode]}</Badge>
              {mirror && <Badge tone="neutral">зеркально</Badge>}
            </div>
            {frozen && (
              <div className="preview-stale" role="status">
                Нет новых кадров {Math.round((now - frame.receivedAt) / 1000)} с — показан последний полученный
              </div>
            )}
          </div>
        ) : (
          <div className="preview-empty" role="status">
            <strong>Нет кадров</strong>
            <span>Ожидаем поток от источника. Камера открывается только сервисом, не интерфейсом.</span>
          </div>
        )}
      </div>
      <div className="preview-meta">
        <span className="mono small">
          {meta ? `кадр ${meta.frame_id} · ${sessionT(meta.t_session_ms)} · ${meta.width}×${meta.height}` : "кадр —"}
        </span>
        <span className="small" aria-live="off">
          {overlayNote("лица", attRel)} · {overlayNote("телефон", phoneRel)}
        </span>
        <label className="check small">
          <input type="checkbox" checked={mirror} onChange={(e) => setMirror(e.target.checked)} />
          зеркально
        </label>
      </div>
    </div>
  );
}

function overlayNote(label: string, r: { draw: boolean; sync: boolean; age: number | null }): string {
  if (r.sync) return `${label}: этот кадр`;
  if (r.age === null) return `${label}: нет разметки`;
  if (r.draw) return `${label}: кадр −${Math.round(r.age)} мс (пунктир)`;
  return `${label}: разметка устарела (${Math.round(r.age)} мс), не показана`;
}
