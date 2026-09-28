const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { URL } = require("url");
const GuacamoleLite = require("../../vendor/guacamole/guacamole-lite");
const Crypt = require("../../vendor/guacamole/guacamole-lite/lib/Crypt.js");
const { applyClientSettings } = require("./rdp-settings");
const { dropAllConnections } = require("./sessions");

const HOST = "127.0.0.1";
const PORT = Number(process.env.REMOTE_GATEWAY_PORT);
if (!Number.isInteger(PORT) || PORT <= 0) {
  throw new Error("REMOTE_GATEWAY_PORT 无效");
}
const GUACD_PORT = Number(process.env.REMOTE_GUACD_PORT);
if (!Number.isInteger(GUACD_PORT) || GUACD_PORT <= 0) {
  throw new Error("REMOTE_GUACD_PORT 无效");
}

const WEB = path.join(__dirname, "web");
const GUAC_JS = path.join(__dirname, "..", "..", "vendor", "guacamole", "guacamole-common-js", "all.min.js");
const KEY = crypto.randomBytes(32);
const crypt = new Crypt("AES-256-CBC", KEY);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

function sendJson(res, status, body) {
  const raw = Buffer.from(JSON.stringify(body), "utf8");
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": raw.length,
  });
  res.end(raw);
}

function sendFile(res, file, ctype) {
  const raw = fs.readFileSync(file);
  res.writeHead(200, {
    "content-type": ctype,
    "content-length": raw.length,
    "cache-control": "no-store",
  });
  res.end(raw);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function connectToken(body) {
  const hostname = String(body.hostname || "").trim();
  const username = String(body.username || "").trim();
  if (!hostname) {
    throw new Error("地址不能为空");
  }
  if (!username) {
    throw new Error("用户名不能为空");
  }
  const port = body.port === undefined || body.port === "" ? 3389 : Number(body.port);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error("端口无效");
  }
  const settings = {
    hostname,
    port: String(port),
    username,
    password: String(body.password || ""),
    security: "any",
    "ignore-cert": true,
    "disable-gfx": true,
    "resize-method": "display-update",
  };
  applyClientSettings(body, settings);
  const domain = String(body.domain || "").trim();
  if (domain) {
    settings.domain = domain;
  }
  return crypt.encrypt({
    connection: {
      type: "rdp",
      settings,
    },
  });
}

function resolveStatic(pathname) {
  if (pathname.includes("..")) {
    throw new Error("非法路径");
  }
  if (pathname === "/" || pathname === "/index.html") {
    return { file: path.join(__dirname, "standalone.html"), ctype: TYPES[".html"] };
  }
  if (pathname === "/all.min.js") {
    return { file: GUAC_JS, ctype: TYPES[".js"] };
  }
  throw new Error("没有这个文件: " + pathname);
}

const server = http.createServer((req, res) => {
  Promise.resolve()
    .then(async () => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "POST" && url.pathname === "/connect") {
        await dropAllConnections(guacamole);
        const body = JSON.parse((await readBody(req)).toString("utf8"));
        sendJson(res, 200, { token: connectToken(body) });
        return;
      }
      if (req.method === "POST" && url.pathname === "/disconnect") {
        const closed = await dropAllConnections(guacamole);
        sendJson(res, 200, { closed });
        return;
      }
      if (req.method !== "GET" && req.method !== "HEAD") {
        throw new Error("不支持的方法: " + req.method);
      }
      const resolved = resolveStatic(url.pathname);
      if (!fs.existsSync(resolved.file)) {
        throw new Error("找不到文件: " + resolved.file);
      }
      if (req.method === "HEAD") {
        const st = fs.statSync(resolved.file);
        res.writeHead(200, {
          "content-type": resolved.ctype,
          "content-length": st.size,
        });
        res.end();
        return;
      }
      sendFile(res, resolved.file, resolved.ctype);
    })
    .catch((err) => {
      if (res.headersSent) {
        res.destroy();
        return;
      }
      sendJson(res, 400, { error: String(err.message || err) });
    });
});

const guacamole = new GuacamoleLite(
  {
    server,
    handleProtocols: (protocols) => {
      if (!protocols.has("guacamole")) {
        throw new Error("缺少 guacamole 子协议");
      }
      return "guacamole";
    },
  },
  {
    host: "127.0.0.1",
    port: GUACD_PORT,
  },
  {
    maxInactivityTime: 0,
    crypt: {
      cypher: "AES-256-CBC",
      key: KEY,
    },
    log: {
      level: "NORMAL",
    },
  },
);

server.listen(PORT, HOST, () => {
  process.stdout.write("[remote] gateway http://" + HOST + ":" + PORT + "/\n");
});
