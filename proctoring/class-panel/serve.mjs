// Qorgau Class panel (T02) — standalone local server. Node.js stdlib only, loopback only.
//
//   node proctoring/class-panel/serve.mjs                       DEMO (config.json: adapter "demo"), http://127.0.0.1:8790/
//   node proctoring/class-panel/serve.mjs --port 8791
//   node proctoring/class-panel/serve.mjs --upstream http://127.0.0.1:8765
//        REAL mode against a running C1 class server: the panel stays on this origin and ONLY the contract
//        paths /api/teacher/* and /ws/teacher are proxied (cookie auth of C1 keeps working same-origin).
//        Open http://127.0.0.1:8790/?adapter=real
//
// In production the panel is meant to be served by C1 itself at "/" (see HANDOFF.md); this script is for
// development and checks.
import { createReadStream, statSync } from "node:fs";
import { createServer, request } from "node:http";
import { connect } from "node:net";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL(".", import.meta.url)));
const args = process.argv.slice(2);
const arg = (name, def) => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : def;
};
const port = Number(arg("--port", process.env.CLASS_PANEL_PORT ?? "8790"));
const upstreamRaw = arg("--upstream", process.env.CLASS_PANEL_UPSTREAM ?? "");
let upstream = null;
if (upstreamRaw) {
  const u = new URL(upstreamRaw);
  if (u.protocol !== "http:") throw new Error("--upstream must be http://host:port");
  upstream = { host: u.hostname, port: Number(u.port || 80) };
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".md": "text/plain; charset=utf-8",
};
const PUBLIC = new Set(["index.html", "styles.css", "config.json"]);

function isProxied(path) {
  return path === "/ws/teacher" || path.startsWith("/api/teacher/") || path === "/api/teacher";
}

const server = createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (upstream && isProxied(url.pathname)) {
    const p = request(
      { host: upstream.host, port: upstream.port, method: req.method, path: url.pathname + url.search, headers: { ...req.headers, host: `${upstream.host}:${upstream.port}` } },
      (r) => {
        res.writeHead(r.statusCode ?? 502, r.headers);
        r.pipe(res);
      },
    );
    p.on("error", () => {
      if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
      res.end("class server unreachable");
    });
    req.pipe(p);
    return;
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405).end();
    return;
  }
  let rel = decodeURIComponent(url.pathname);
  if (rel === "/") rel = "/index.html";
  const file = normalize(join(root, rel));
  const relToRoot = file.slice(root.length + 1);
  const allowed = file.startsWith(root + sep) && (PUBLIC.has(relToRoot) || relToRoot.startsWith(`src${sep}`));
  let st = null;
  try {
    st = allowed ? statSync(file) : null;
  } catch {
    st = null;
  }
  if (!st || !st.isFile()) {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" }).end("not found");
    return;
  }
  res.writeHead(200, {
    "content-type": MIME[extname(file)] ?? "application/octet-stream",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  });
  if (req.method === "HEAD") res.end();
  else createReadStream(file).pipe(res);
});

// WebSocket upgrade: only /ws/teacher, only with --upstream
server.on("upgrade", (req, socket, head) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (!upstream || url.pathname !== "/ws/teacher") {
    socket.end("HTTP/1.1 404 Not Found\r\n\r\n");
    return;
  }
  const up = connect(upstream.port, upstream.host, () => {
    const headers = { ...req.headers, host: `${upstream.host}:${upstream.port}` };
    const lines = [`${req.method} ${url.pathname}${url.search} HTTP/1.1`, ...Object.entries(headers).map(([k, v]) => `${k}: ${v}`), "", ""];
    up.write(lines.join("\r\n"));
    if (head?.length) up.write(head);
    up.pipe(socket);
    socket.pipe(up);
  });
  up.on("error", () => socket.destroy());
  socket.on("error", () => up.destroy());
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Qorgau Class panel (T02): http://127.0.0.1:${port}/  ${upstream ? `REAL proxy → ${upstream.host}:${upstream.port} (open ?adapter=real)` : "(adapter from config.json; DEMO by default)"}`);
});
