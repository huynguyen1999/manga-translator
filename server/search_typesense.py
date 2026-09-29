"""Typesense client wrapper for schema lifecycle, batch indexing, and hybrid retrieval."""
from __future__ import annotations

import logging
import os
from typing import Any

from server.search_embeddings import CANDIDATE_LIMIT, COLLECTION, DIMENSIONS, PROFILE

logger = logging.getLogger("manga-translator.search.typesense")


class SearchTypesense:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        protocol: str | None = None,
        api_key: str | None = None,
        collection_name: str | None = None,
        timeout: int = 15,
        client: Any = None,
    ):
        self.host = host or os.getenv("TYPESENSE_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("TYPESENSE_PORT", "8108"))
        self.protocol = protocol or os.getenv("TYPESENSE_PROTOCOL", "http")
        self.api_key = api_key or os.getenv("TYPESENSE_API_KEY", "xyz")
        self.collection_name = collection_name or os.getenv("TYPESENSE_COLLECTION", COLLECTION)
        self.timeout = timeout
        self._client = client

    def get_client(self):
        if self._client is None:
            try:
                import typesense
            except ImportError as error:
                raise RuntimeError("Install requirements-search.txt to enable Search Lab.") from error
            self._client = typesense.AsyncClient({
                "nodes": [{
                    "host": self.host,
                    "port": str(self.port),
                    "protocol": self.protocol,
                }],
                "api_key": self.api_key,
                "connection_timeout_seconds": self.timeout,
            })
        return self._client

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "name": self.collection_name,
            "fields": [
                {"name": "id", "type": "string"},
                {"name": "groupId", "type": "string", "facet": True},
                {"name": "sourceKey", "type": "string", "facet": True},
                {"name": "title", "type": "string"},
                {"name": "content", "type": "string"},
                {"name": "chunkIndex", "type": "int32"},
                {"name": "start", "type": "int32"},
                {"name": "end", "type": "int32"},
                {"name": "fingerprint", "type": "string"},
                {"name": "profile", "type": "string", "facet": True},
                {"name": "published", "type": "bool"},
                {"name": "embedding", "type": "float[]", "num_dim": DIMENSIONS["summary"]},
            ],
        }

    async def health(self) -> bool:
        client = self.get_client()
        try:
            return await client.operations.is_healthy()
        except Exception:
            return False

    async def ensure_collection(self, reset: bool = False) -> None:
        client = self.get_client()
        exists = False
        try:
            await client.collections[self.collection_name].retrieve()
            exists = True
        except Exception:
            exists = False

        if reset and exists:
            try:
                await client.collections[self.collection_name].delete()
            except Exception:
                pass
            exists = False

        if not exists:
            await client.collections.create(self.schema)

        # Discard any interrupted/unpublished document staging
        try:
            await client.collections[self.collection_name].documents.delete({"filter_by": "published:=false"})
        except Exception:
            pass

    async def upsert_chunks(self, documents: list[dict[str, Any]]) -> None:
        if not documents:
            return
        client = self.get_client()
        await client.collections[self.collection_name].documents.import_(documents, {"action": "upsert"})

    async def publish_chunks(self, ids: list[str], group_id: str) -> None:
        if not ids:
            return
        client = self.get_client()
        updates = [{"id": doc_id, "published": True, "groupId": group_id} for doc_id in ids]
        await client.collections[self.collection_name].documents.import_(updates, {"action": "update"})

    async def delete_documents(self, ids: list[str]) -> None:
        if not ids:
            return
        client = self.get_client()
        filter_str = f"id:[{','.join(ids)}]"
        await client.collections[self.collection_name].documents.delete({"filter_by": filter_str})

    async def delete_source(self, source_key: str) -> None:
        client = self.get_client()
        await client.collections[self.collection_name].documents.delete({"filter_by": f"sourceKey:={source_key}"})

    async def delete_source_except(self, source_key: str, keep_ids: list[str]) -> None:
        client = self.get_client()
        filter_str = f"sourceKey:={source_key}"
        if keep_ids:
            filter_str += f" && id:!=[{','.join(keep_ids)}]"
        await client.collections[self.collection_name].documents.delete({"filter_by": filter_str})

    async def delete_group(self, group_id: str) -> None:
        client = self.get_client()
        await client.collections[self.collection_name].documents.delete({"filter_by": f"groupId:={group_id}"})

    async def retrieve_documents(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        client = self.get_client()
        res = await client.collections[self.collection_name].documents.search({
            "q": "*",
            "filter_by": f"id:[{','.join(ids)}]",
            "limit": max(len(ids), 10),
            "exclude_fields": "embedding",
        })
        return [hit["document"] for hit in res.get("hits", []) if "document" in hit]

    async def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        group_ids: list[str] | None = None,
        limit: int = CANDIDATE_LIMIT,
        num_typos: int = 2,
        alpha: float = 0.7,
        hybrid_rerank: bool = True,
        profile: str = PROFILE,
    ) -> dict[str, list[dict[str, Any]]]:
        client = self.get_client()
        filter_expr = f"published:=true && profile:={profile}"
        if group_ids is not None:
            if not group_ids:
                return {}
            filter_expr += f" && groupId:[{','.join(group_ids)}]"

        vector_str = f"embedding:([{','.join(f'{x:.6f}' for x in query_vector)}], alpha: {alpha})"
        search_params: dict[str, Any] = {
            "collection": self.collection_name,
            "q": query.strip() or "*",
            "query_by": "title,content",
            "vector_query": vector_str,
            "rerank_hybrid_matches": hybrid_rerank,
            "group_by": "groupId",
            "group_limit": 1,
            "limit": limit,
            "num_typos": max(0, min(2, num_typos)),
            "filter_by": filter_expr,
            "exclude_fields": "embedding",
        }

        # Multi-search uses POST with JSON payload, avoiding GET query string length limits with 768-dim embeddings.
        multi_response = await client.multi_search.perform({"searches": [search_params]}, {})
        response = multi_response.get("results", [{}])[0]
        grouped_hits = response.get("grouped_hits", [])
        result: dict[str, list[dict[str, Any]]] = {}

        for group in grouped_hits:
            group_keys = group.get("group_key") or []
            group_id = str(group_keys[0]) if group_keys else ""
            hits = group.get("hits") or []
            group_results = []
            for hit in hits:
                doc = hit.get("document", {})
                vec_dist = hit.get("vector_distance")
                text_match = hit.get("text_match")
                hybrid_info = hit.get("hybrid_search_info") or {}
                rank_fusion = hybrid_info.get("rank_fusion_score")

                if rank_fusion is not None:
                    score = float(rank_fusion)
                elif vec_dist is not None:
                    score = max(0.0, 1.0 - float(vec_dist))
                else:
                    score = 1.0

                item = {
                    **doc,
                    "excerpt": doc.get("content"),
                    "score": score,
                    "vectorDistance": float(vec_dist) if vec_dist is not None else None,
                    "textMatch": int(text_match) if text_match is not None else None,
                    "rankFusionScore": float(rank_fusion) if rank_fusion is not None else None,
                    "highlight": hit.get("highlight", {}),
                }
                group_results.append(item)
            if group_id and group_results:
                result[group_id] = group_results

        return result

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.api_call.aclose()
            except Exception:
                pass
            self._client = None
