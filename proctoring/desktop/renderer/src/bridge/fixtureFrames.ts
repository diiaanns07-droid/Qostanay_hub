// Draws SYNTHETIC preview frames for the FixtureBridge. No camera is ever opened: these are vector drawings
// with a burned-in "FIXTURE" label, encoded to JPEG so the renderer exercises the same binary preview path.
import type { BBox } from "@contracts/qorgau-v1.generated";

export const FRAME_W = 640;
export const FRAME_H = 480;

export interface SceneSpec {
  frameId: number;
  tSessionMs: number;
  faces: BBox[];
  /** Head tilted down (gaze episode): draws the face lower with eyes looking down. */
  lookingDown: boolean;
  phone: BBox | null;
}

type Ctx = OffscreenCanvasRenderingContext2D | CanvasRenderingContext2D;

function makeCanvas(): { ctx: Ctx; toJpeg: () => Promise<Uint8Array> } | null {
  if (typeof OffscreenCanvas !== "undefined") {
    const c = new OffscreenCanvas(FRAME_W, FRAME_H);
    const ctx = c.getContext("2d");
    if (!ctx) return null;
    return {
      ctx,
      toJpeg: async () => new Uint8Array(await (await c.convertToBlob({ type: "image/jpeg", quality: 0.72 })).arrayBuffer()),
    };
  }
  if (typeof document === "undefined") return null;
  const c = document.createElement("canvas");
  c.width = FRAME_W;
  c.height = FRAME_H;
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  return {
    ctx,
    toJpeg: () =>
      new Promise((resolve, reject) =>
        c.toBlob(
          (b) => (b ? b.arrayBuffer().then((a) => resolve(new Uint8Array(a)), reject) : reject(new Error("toBlob failed"))),
          "image/jpeg",
          0.72,
        ),
      ),
  };
}

let shared: ReturnType<typeof makeCanvas> | undefined;

export async function renderFixtureFrame(spec: SceneSpec): Promise<Uint8Array | null> {
  if (shared === undefined) shared = makeCanvas();
  if (!shared) return null;
  const { ctx, toJpeg } = shared;
  const W = FRAME_W;
  const H = FRAME_H;

  // Room
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, "#3a4047");
  g.addColorStop(1, "#272b30");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "#4a3f36";
  ctx.fillRect(0, H * 0.8, W, H * 0.2); // desk

  spec.faces.forEach((b, i) => drawPerson(ctx, b, i === 0 && spec.lookingDown, i === 0));
  if (spec.phone) drawPhone(ctx, spec.phone);

  // Burned-in label: this is never a camera image.
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(0, 0, W, 30);
  ctx.fillStyle = "#f5d36b";
  ctx.font = "bold 15px system-ui, sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillText("SYNTHETIC · FIXTURE · не камера", 10, 15);
  ctx.fillStyle = "#e6e6e6";
  ctx.font = "13px ui-monospace, monospace";
  const label = `frame ${spec.frameId} · t ${(spec.tSessionMs / 1000).toFixed(1)} s`;
  ctx.fillText(label, W - 10 - ctx.measureText(label).width, 15);
  // Orientation marker: unmirrored frame, the subject's LEFT is on the image RIGHT.
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  ctx.font = "12px system-ui, sans-serif";
  ctx.fillText("◀ правая сторона студента", 10, H - 14);
  const r = "левая сторона студента ▶";
  ctx.fillText(r, W - 10 - ctx.measureText(r).width, H - 14);

  try {
    return await toJpeg();
  } catch {
    return null;
  }
}

function drawPerson(ctx: Ctx, b: BBox, down: boolean, primary: boolean): void {
  const W = FRAME_W;
  const H = FRAME_H;
  const x = b.x_min * W;
  const y = b.y_min * H;
  const w = (b.x_max - b.x_min) * W;
  const h = (b.y_max - b.y_min) * H;
  const cx = x + w / 2;
  // Shoulders
  ctx.fillStyle = primary ? "#5b6b78" : "#6d5f73";
  ctx.beginPath();
  ctx.ellipse(cx, y + h * 1.55, w * 0.95, h * 0.6, 0, Math.PI, 0);
  ctx.fill();
  // Head
  ctx.fillStyle = "#c9a88c";
  ctx.beginPath();
  ctx.ellipse(cx, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
  ctx.fill();
  // Hair
  ctx.fillStyle = "#3b2d24";
  ctx.beginPath();
  ctx.ellipse(cx, y + h * 0.22, w * 0.5, h * 0.24, 0, Math.PI, 0);
  ctx.fill();
  // Eyes
  const ey = y + h * (down ? 0.52 : 0.45);
  ctx.fillStyle = "#2b2b2b";
  for (const dx of [-0.18, 0.18]) {
    ctx.beginPath();
    ctx.ellipse(cx + dx * w, ey + (down ? h * 0.03 : 0), w * 0.05, h * (down ? 0.015 : 0.035), 0, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawPhone(ctx: Ctx, b: BBox): void {
  const x = b.x_min * FRAME_W;
  const y = b.y_min * FRAME_H;
  const w = (b.x_max - b.x_min) * FRAME_W;
  const h = (b.y_max - b.y_min) * FRAME_H;
  ctx.fillStyle = "#111";
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, 6);
  ctx.fill();
  ctx.fillStyle = "#28465a";
  ctx.fillRect(x + w * 0.1, y + h * 0.08, w * 0.8, h * 0.84);
  // hand
  ctx.fillStyle = "#c9a88c";
  ctx.beginPath();
  ctx.ellipse(x + w / 2, y + h * 0.95, w * 0.8, h * 0.18, 0, 0, Math.PI * 2);
  ctx.fill();
}
