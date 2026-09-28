const rawOrigin = location.origin;
const parsed = new URL(rawOrigin);
const wsScheme = parsed.protocol === "https:" ? "wss" : "ws";

export const API_BASE = rawOrigin;
export const WS_URL = wsScheme + "://" + parsed.host + "/api/v1/ws";
