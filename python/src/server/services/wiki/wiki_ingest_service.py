"""
Wiki Ingest Service — Transform RAG sources into structured wiki pages.

This is the bridge layer between RAG chunks and the wiki knowledge graph.
It uses LLM to extract entities, concepts, and facts from source material,
then creates wiki pages with auto-generated cross-links.

Flow:
  RAG source → get chunks/pages → LLM extraction → wiki pages + links
"""

import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from ...utils import get_supabase_client
from ..wiki.wiki_service import WikiService, slugify

logger = logging.getLogger(__name__)

# Extraction prompt template for LLM
EXTRACTION_PROMPT = """Analyze the following source content and extract structured knowledge.

Source Title: {title}
Source URL: {url}

Content:
{content}

Extract the following as JSON:
{{
  "summary": "1-2 sentence summary of the entire source",
  "entities": [
    {{
      "name": "Entity name (tool, person, org, project)",
      "category": "tool|framework|library|service|people|protocol|methodology",
      "description": "2-3 sentence description",
      "tags": ["tag1", "tag2"]
    }}
  ],
  "concepts": [
    {{
      "name": "Concept name (pattern, technique, principle)",
      "category": "pattern|architecture|api|infra|methodology",
      "description": "2-3 sentence description",
      "tags": ["tag1", "tag2"]
    }}
  ],
  "key_facts": [
    "Important fact or finding 1",
    "Important fact or finding 2"
  ],
  "contradictions": [
    {{
      "claim_a": "What this source says",
      "claim_b": "What might conflict with existing knowledge",
      "topic": "Topic of contradiction"
    }}
  ]
}}

Rules:
- Extract 2-10 entities (things: tools, libraries, people, organizations)
- Extract 2-10 concepts (ideas: patterns, architectures, techniques)
- Keep descriptions factual, cite the source, never fabricate
- Tags should be lowercase, 1-2 words each
- Only flag contradictions if the content explicitly disagrees with common knowledge
- Return valid JSON only, no markdown fences
"""


class WikiIngestService:
    """Service for extracting wiki pages from RAG sources."""

    def __init__(self) -> None:
        self.supabase = get_supabase_client()
        self.wiki = WikiService()

    # ── E1: Content-Hash Caching ─────────────────────────────

    def _compute_content_hash(self, source_id: str, content: str) -> str:
        """SHA256(source_id + content) for cache invalidation."""
        return hashlib.sha256(f"{source_id}:{content}".encode("utf-8")).hexdigest()

    def _get_stored_hash(self, source_id: str) -> str | None:
        """Read wiki_ingest_hash from archon_sources.metadata."""
        try:
            result = (
                self.supabase.table("archon_sources")
                .select("metadata")
                .eq("source_id", source_id)
                .single()
                .execute()
            )
            metadata = result.data.get("metadata", {}) if result.data else {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            return metadata.get("wiki_ingest_hash")
        except Exception:
            return None

    def _store_content_hash(self, source_id: str, hash_value: str) -> None:
        """Write wiki_ingest_hash into archon_sources.metadata."""
        try:
            result = (
                self.supabase.table("archon_sources")
                .select("metadata")
                .eq("source_id", source_id)
                .single()
                .execute()
            )
            metadata = result.data.get("metadata", {}) if result.data else {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            metadata["wiki_ingest_hash"] = hash_value
            self.supabase.table("archon_sources").update(
                {"metadata": json.dumps(metadata)}
            ).eq("source_id", source_id).execute()
        except Exception as e:
            logger.warning(f"Failed to store content hash: {e}")

    async def ingest_source(
        self,
        source_id: str,
        project_id: str,
        llm_provider: Any | None = None,
        force: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Ingest a RAG source into wiki pages.

        Args:
            source_id: The archon_sources.source_id to ingest
            project_id: Target project for wiki pages
            llm_provider: Optional LLM provider for extraction.
                         If None, uses rule-based extraction (simpler).
            force: If True, bypass content-hash cache and re-extract.

        Returns:
            (success, {pages_created, links_created, source_summary})
        """
        try:
            # 1. Get source metadata
            source = self._get_source(source_id)
            if not source:
                return False, {"error": f"Source {source_id} not found"}

            # 2. Get page content for this source
            pages = self._get_source_pages(source_id)
            if not pages:
                return False, {"error": f"No pages found for source {source_id}"}

            # 3. Build content for extraction
            content = self._build_extraction_content(source, pages)

            # E1: Content-hash cache check
            if not force:
                new_hash = self._compute_content_hash(source_id, content)
                stored_hash = self._get_stored_hash(source_id)
                if stored_hash == new_hash:
                    return True, {"skipped": True, "reason": "content_unchanged", "source_id": source_id}

            # 4. Extract using LLM or rule-based
            if llm_provider:
                extraction = await self._llm_extract(content, source, llm_provider)
            else:
                extraction = self._rule_based_extract(content, source, pages)

            # 5. Create wiki pages
            created_pages = []

            # Source summary page
            summary_page = self._create_source_summary(
                project_id, source_id, source, extraction
            )
            if summary_page:
                created_pages.append(summary_page)

            # Entity pages
            for entity in extraction.get("entities", []):
                page = self._create_entity_page(
                    project_id, source_id, entity
                )
                if page:
                    created_pages.append(page)

            # Concept pages
            for concept in extraction.get("concepts", []):
                page = self._create_concept_page(
                    project_id, source_id, concept
                )
                if page:
                    created_pages.append(page)

            # 6. Auto-create links between new pages
            links_created = self._auto_link_new_pages(created_pages)

            # 7. Link to existing wiki pages
            links_created += self._link_to_existing(project_id, created_pages)

            # 8. Handle contradictions
            for contradiction in extraction.get("contradictions", []):
                self._flag_contradiction(project_id, contradiction, created_pages)

            # E1: Store content hash after successful extraction
            if not force:
                self._store_content_hash(source_id, new_hash)

            return True, {
                "pages_created": len(created_pages),
                "links_created": links_created,
                "source_id": source_id,
                "page_ids": [p["id"] for p in created_pages],
                "entities": len(extraction.get("entities", [])),
                "concepts": len(extraction.get("concepts", [])),
            }

        except Exception as e:
            logger.error(f"Error ingesting source {source_id}: {e}", exc_info=True)
            return False, {"error": str(e)}

    # ── Data Fetching ─────────────────────────────────────────

    def _get_source(self, source_id: str) -> dict | None:
        """Get source metadata from archon_sources."""
        try:
            result = (
                self.supabase.table("archon_sources")
                .select("*")
                .eq("source_id", source_id)
                .single()
                .execute()
            )
            return result.data
        except Exception:
            return None

    def _get_source_pages(self, source_id: str) -> list[dict]:
        """Get all page metadata for a source."""
        try:
            result = (
                self.supabase.table("archon_page_metadata")
                .select("id, url, full_content, section_title, word_count")
                .eq("source_id", source_id)
                .order("section_order")
                .execute()
            )
            return result.data or []
        except Exception:
            return []

    def _build_extraction_content(
        self, source: dict, pages: list[dict]
    ) -> str:
        """Build a consolidated content string for extraction, respecting token limits."""
        parts = []
        total_words = 0
        max_words = 8000  # ~10K tokens limit for extraction context

        for page in pages:
            content = page.get("full_content", "")
            word_count = page.get("word_count", 0) or len(content.split())

            if total_words + word_count > max_words:
                # Truncate this page
                remaining = max_words - total_words
                words = content.split()[:remaining]
                parts.append(" ".join(words))
                break

            parts.append(content)
            total_words += word_count

        return "\n\n---\n\n".join(parts)

    # ── Extraction ────────────────────────────────────────────

    async def _llm_extract(
        self, content: str, source: dict, llm_provider: Any
    ) -> dict:
        """Extract entities/concepts using LLM."""
        try:
            prompt = EXTRACTION_PROMPT.format(
                title=source.get("title", "Unknown"),
                url=source.get("source_url", ""),
                content=content[:15000],  # Hard limit
            )

            # Call LLM provider (compatible with Archon's llm_provider_service)
            response = await llm_provider.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-4o-mini",  # Use fast model for extraction
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            result_text = response.choices[0].message.content
            return json.loads(result_text)

        except Exception as e:
            logger.warning(f"LLM extraction failed, falling back to rule-based: {e}")
            return self._rule_based_extract(content, source, [])

    def _rule_based_extract(
        self, content: str, source: dict, pages: list[dict]
    ) -> dict:
        """Simple rule-based extraction when LLM is not available."""
        title = source.get("title", "")
        summary = source.get("summary", "")
        tags_raw = source.get("metadata", {})
        if isinstance(tags_raw, str):
            try:
                tags_raw = json.loads(tags_raw)
            except Exception:
                tags_raw = {}
        tags = tags_raw.get("tags", [])

        # Create one entity for the source itself
        entities = [{
            "name": title or source.get("source_display_name", "Unknown Source"),
            "category": tags_raw.get("knowledge_type", "tool"),
            "description": summary or f"Documentation source: {source.get('source_url', '')}",
            "tags": tags[:5] if isinstance(tags, list) else [],
        }]

        # Create concepts from page sections
        concepts = []
        for page in pages[:5]:
            section = page.get("section_title", "")
            if section and len(section) > 3:
                concepts.append({
                    "name": section,
                    "category": "pattern",
                    "description": f"Section from {title}: {section}",
                    "tags": tags[:3] if isinstance(tags, list) else [],
                })

        return {
            "summary": summary or f"Source: {title}",
            "entities": entities,
            "concepts": concepts[:10],
            "key_facts": [],
            "contradictions": [],
        }

    # ── Page Creation ─────────────────────────────────────────

    def _create_source_summary(
        self,
        project_id: str,
        source_id: str,
        source: dict,
        extraction: dict,
    ) -> dict | None:
        """Create a source summary wiki page."""
        title = source.get("title", source.get("source_display_name", "Unknown"))
        summary = extraction.get("summary", "")
        key_facts = extraction.get("key_facts", [])

        content_parts = [f"# {title}\n"]
        if summary:
            content_parts.append(f"{summary}\n")
        if source.get("source_url"):
            content_parts.append(f"**Source:** {source['source_url']}\n")
        if key_facts:
            content_parts.append("## Key Facts\n")
            for fact in key_facts:
                content_parts.append(f"- {fact}")

        ok, page = self.wiki.create_page(
            project_id=project_id,
            title=f"Source: {title}",
            content="\n".join(content_parts),
            page_type="source_summary",
            source_ids=[source_id],
            summary=summary,
            status="active",
        )
        return page if ok else None

    @staticmethod
    def _parse_jsonb_list(value: Any) -> list:
        """Safely parse a JSONB field that may be a list or a JSON string."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def _create_entity_page(
        self,
        project_id: str,
        source_id: str,
        entity: dict,
    ) -> dict | None:
        """Create or update an entity wiki page."""
        name = entity.get("name", "")
        if not name:
            return None

        # Check if page already exists (upsert logic)
        slug = slugify(name)
        ok, existing = self.wiki.get_page(
            slug=slug, project_id=project_id, include_links=False
        )

        if ok and existing:
            # Re-fetch fresh page data to avoid stale reads
            ok_fresh, fresh = self.wiki.get_page(
                page_id=existing["id"], include_links=False
            )
            page_data = fresh if ok_fresh else existing

            # Merge content
            existing_content = page_data.get("content", "")
            new_content = f"{existing_content}\n\n## From additional source\n\n{entity.get('description', '')}"

            # Merge source_ids
            current_sources = self._parse_jsonb_list(page_data.get("source_ids"))
            if source_id not in current_sources:
                current_sources.append(source_id)

            # Merge tags — read current from fresh data, union with new
            current_tags = self._parse_jsonb_list(page_data.get("tags"))
            new_tags = entity.get("tags", [])
            merged_tags = list(set(current_tags + new_tags))

            self.wiki.update_page(
                page_id=page_data["id"],
                content=new_content,
                tags=merged_tags,
            )
            return page_data

        # Create new
        ok, page = self.wiki.create_page(
            project_id=project_id,
            title=name,
            content=f"# {name}\n\n{entity.get('description', '')}",
            page_type="entity",
            category=entity.get("category", "tool"),
            tags=entity.get("tags", []),
            source_ids=[source_id],
            summary=entity.get("description", ""),
            status="active",
        )
        return page if ok else None

    def _create_concept_page(
        self,
        project_id: str,
        source_id: str,
        concept: dict,
    ) -> dict | None:
        """Create or update a concept wiki page."""
        name = concept.get("name", "")
        if not name:
            return None

        slug = slugify(name)
        ok, existing = self.wiki.get_page(
            slug=slug, project_id=project_id, include_links=False
        )

        if ok and existing:
            # Re-fetch fresh data
            ok_fresh, fresh = self.wiki.get_page(
                page_id=existing["id"], include_links=False
            )
            page_data = fresh if ok_fresh else existing

            # Merge content
            existing_content = page_data.get("content", "")
            new_content = f"{existing_content}\n\n## Additional context\n\n{concept.get('description', '')}"

            # Merge tags from fresh data + new concept
            current_tags = self._parse_jsonb_list(page_data.get("tags"))
            new_tags = concept.get("tags", [])
            merged_tags = list(set(current_tags + new_tags))

            # Merge source_ids
            current_sources = self._parse_jsonb_list(page_data.get("source_ids"))
            if source_id not in current_sources:
                current_sources.append(source_id)

            self.wiki.update_page(
                page_id=page_data["id"],
                content=new_content,
                tags=merged_tags,
            )
            return page_data

        ok, page = self.wiki.create_page(
            project_id=project_id,
            title=name,
            content=f"# {name}\n\n{concept.get('description', '')}",
            page_type="concept",
            category=concept.get("category", "pattern"),
            tags=concept.get("tags", []),
            source_ids=[source_id],
            summary=concept.get("description", ""),
            status="active",
        )
        return page if ok else None

    # ── Auto-Linking ──────────────────────────────────────────

    def _auto_link_new_pages(self, pages: list[dict]) -> int:
        """Create links between newly created pages (same-source siblings)."""
        links = 0
        for i, page_a in enumerate(pages):
            for page_b in pages[i + 1:]:
                if page_a.get("id") and page_b.get("id") and page_a["id"] != page_b["id"]:
                    ok, _ = self.wiki.create_link(
                        from_page_id=page_a["id"],
                        to_page_id=page_b["id"],
                        link_type="related",
                        context="From same source ingestion",
                        confidence="inferred",
                        created_by="system",
                    )
                    if ok:
                        links += 1
        return links

    def _link_to_existing(self, project_id: str, new_pages: list[dict]) -> int:
        """Link new pages to existing wiki pages by tag overlap."""
        links = 0
        for page in new_pages:
            if not page or not page.get("id"):
                continue

            page_tags = page.get("tags", [])
            if isinstance(page_tags, str):
                try:
                    page_tags = json.loads(page_tags)
                except Exception:
                    page_tags = []

            if not page_tags:
                continue

            # Find existing pages with overlapping tags
            try:
                for tag in page_tags[:3]:
                    result = (
                        self.supabase.table("archon_wiki_pages")
                        .select("id, tags")
                        .eq("project_id", project_id)
                        .neq("id", page["id"])
                        .contains("tags", json.dumps([tag]))
                        .limit(5)
                        .execute()
                    )
                    for match in result.data or []:
                        ok, _ = self.wiki.create_link(
                            from_page_id=page["id"],
                            to_page_id=match["id"],
                            link_type="related",
                            context=f"Shared tag: {tag}",
                            confidence="inferred",
                            created_by="system",
                        )
                        if ok:
                            links += 1
            except Exception as e:
                logger.warning(f"Error linking to existing pages: {e}")

        return links

    def _flag_contradiction(
        self,
        project_id: str,
        contradiction: dict,
        pages: list[dict],
    ) -> None:
        """Flag a detected contradiction between new and existing knowledge."""
        try:
            topic = contradiction.get("topic", "Unknown")
            claim_a = contradiction.get("claim_a", "")
            claim_b = contradiction.get("claim_b", "")

            # Search for existing pages about this topic
            ok, results = self.wiki.search_pages(
                query=topic, project_id=project_id, limit=3
            )

            if ok and results:
                for existing in results:
                    # Find one of the new pages to link from
                    new_page = next((p for p in pages if p and p.get("id")), None)
                    if new_page and existing.get("id"):
                        self.wiki.create_link(
                            from_page_id=new_page["id"],
                            to_page_id=existing["id"],
                            link_type="contradicts",
                            context=f"New source claims: {claim_a[:100]}. Existing claims: {claim_b[:100]}",
                            confidence="extracted",
                            strength=0.8,
                            created_by="system",
                        )
        except Exception as e:
            logger.warning(f"Error flagging contradiction: {e}")
