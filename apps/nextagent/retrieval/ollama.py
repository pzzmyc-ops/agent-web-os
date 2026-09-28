from __future__ import annotations

import httpx

EMBED_BATCH = 32
CHECK_TIMEOUT = 5.0
EMBED_TIMEOUT = 180.0


class OllamaUnavailable(RuntimeError):
    pass


class OllamaEmbedder:
    def __init__(self, url: str, model: str) -> None:
        self.url = url.rstrip("/")
        self.model = model

    def pull_hint(self) -> str:
        return f"请先启动 Ollama,并执行: ollama pull {self.model}"

    async def check(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, trust_env=False) as client:
                resp = await client.get(f"{self.url}/api/tags")
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"连不上 Ollama({self.url}): {exc}。{self.pull_hint()}") from exc
        if resp.status_code != 200:
            raise OllamaUnavailable(f"Ollama 返回 {resp.status_code}: {resp.text[:200]}。{self.pull_hint()}")
        models = resp.json().get("models") or []
        names = {str(m.get("name") or "") for m in models}
        bare = {n.split(":")[0] for n in names}
        want = self.model
        if want not in names and want.split(":")[0] not in bare:
            raise OllamaUnavailable(
                f"Ollama 里没有模型 {want}(已有: {', '.join(sorted(names)) or '无'})。{self.pull_hint()}"
            )
        return {"url": self.url, "model": want, "available_models": sorted(names)}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=EMBED_TIMEOUT, trust_env=False) as client:
            for start in range(0, len(texts), EMBED_BATCH):
                batch = texts[start:start + EMBED_BATCH]
                try:
                    resp = await client.post(
                        f"{self.url}/api/embed",
                        json={"model": self.model, "input": batch, "truncate": True},
                    )
                except httpx.HTTPError as exc:
                    raise OllamaUnavailable(f"Ollama 嵌入请求失败: {exc}。{self.pull_hint()}") from exc
                if resp.status_code != 200:
                    raise OllamaUnavailable(f"Ollama 嵌入返回 {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                vectors = data.get("embeddings")
                if not isinstance(vectors, list) or len(vectors) != len(batch):
                    raise RuntimeError(
                        f"Ollama 嵌入结果数量不符: 送 {len(batch)} 条,回 {len(vectors) if isinstance(vectors, list) else '非列表'}"
                    )
                out.extend(vectors)
        return out
