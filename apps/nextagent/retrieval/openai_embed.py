from __future__ import annotations

import httpx

EMBED_BATCH = 32
CHECK_TIMEOUT = 15.0
EMBED_TIMEOUT = 180.0


class EmbedderUnavailable(RuntimeError):
    pass


class OpenAIEmbedder:
    def __init__(self, url: str, api_key: str, model: str) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def check(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, trust_env=False) as client:
                resp = await client.get(f"{self.url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise EmbedderUnavailable(f"连不上嵌入服务({self.url}): {exc}") from exc
        if resp.status_code != 200:
            raise EmbedderUnavailable(f"嵌入服务返回 {resp.status_code}: {resp.text[:200]}")
        models = resp.json().get("data") or []
        names: set[str] = set()
        for m in models:
            mid = str(m.get("id") or "")
            names.add(mid)
            quant = str(m.get("quant") or "")
            if quant:
                names.add(f"{mid}:{quant}")
        if self.model not in names:
            raise EmbedderUnavailable(
                f"嵌入服务里没有模型 {self.model}(已有: {', '.join(sorted(names)) or '无'})"
            )
        return {"url": self.url, "model": self.model, "available_models": sorted(names)}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=EMBED_TIMEOUT, trust_env=False) as client:
            for start in range(0, len(texts), EMBED_BATCH):
                batch = texts[start:start + EMBED_BATCH]
                try:
                    resp = await client.post(
                        f"{self.url}/embeddings",
                        headers=self._headers(),
                        json={"model": self.model, "input": batch},
                    )
                except httpx.HTTPError as exc:
                    raise EmbedderUnavailable(f"嵌入请求失败({self.url}): {exc}") from exc
                if resp.status_code != 200:
                    raise EmbedderUnavailable(f"嵌入服务返回 {resp.status_code}: {resp.text[:300]}")
                items = resp.json().get("data")
                if not isinstance(items, list) or len(items) != len(batch):
                    raise RuntimeError(
                        f"嵌入结果数量不符: 送 {len(batch)} 条,回 {len(items) if isinstance(items, list) else '非列表'}"
                    )
                items = sorted(items, key=lambda d: int(d["index"]))
                out.extend(item["embedding"] for item in items)
        return out
