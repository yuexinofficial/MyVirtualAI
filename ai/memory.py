"""
Long-term memory module — SQLite for structured storage + ChromaDB for semantic search.
Designed for a desktop AI companion: zero-config, file-based, local-only.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime

# Disable ChromaDB telemetry (blocked in China, spams logs with proxy errors)
os.environ["ANONYMIZED_TELEMETRY"] = "False"
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Patterns for automatic fact extraction
FACT_PATTERNS = [
    (r"我(?:叫|是|的名字是)(.+?)(?:[，。！？\s]|$)", "identity"),
    (r"我(?:喜欢|爱|热爱|偏好)(.+?)(?:[，。！？\s]|$)", "preference"),
    (r"我(?:讨厌|不喜欢|厌恶)(.+?)(?:[，。！？\s]|$)", "preference"),
    (r"我(?:住在|在|位于)(.+?)(?:[，。！？\s]|$)", "location"),
    (r"我(?:的|是)(.+?工作|职业|学生|上班族)(?:[，。！？\s]|$)", "occupation"),
    (r"我(?:有|养了|养着)(.+?)(?:[，。！？\s]|$)", "possession"),
    (r"我(?:的)(.+?)(?:是)(.+?)(?:[，。！？\s]|$)", "attribute"),
]


class MemoryStore:
    """Hybrid memory: SQLite for facts/logs + optional ChromaDB for semantic search."""

    def __init__(self, db_path: str = "data/memory.db", use_chromadb: bool = True):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_sqlite()

        self._chroma = None
        self._use_chromadb = use_chromadb
        if use_chromadb:
            self._init_chromadb()

    def _init_sqlite(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content TEXT NOT NULL,
                expression TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_conv_timestamp ON conversations(timestamp);

            CREATE TABLE IF NOT EXISTS session_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                summary TEXT NOT NULL,
                key_topics TEXT
            );

            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL UNIQUE,
                category TEXT DEFAULT 'general',
                source_session TEXT,
                created_at TEXT NOT NULL,
                last_recalled TEXT,
                recall_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS memory_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chroma_id TEXT UNIQUE,
                content_hash TEXT UNIQUE,
                content TEXT NOT NULL,
                content_type TEXT NOT NULL,
                source_session TEXT,
                created_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def _init_chromadb(self):
        try:
            import chromadb
            chroma_path = self._db_path.parent / "chroma"
            self._chroma_client = chromadb.PersistentClient(path=str(chroma_path))
            self._chroma = self._chroma_client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )
            log.info("ChromaDB ready")
        except ImportError:
            log.warning("chromadb not installed — semantic search disabled")
            self._use_chromadb = False
        except Exception as e:
            log.warning(f"ChromaDB init failed: {e} — using keyword fallback")
            self._use_chromadb = False

    # ========== Write ==========

    def save_turn(self, session_id: str, role: str, content: str, expression: str = None):
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO conversations (timestamp, session_id, role, content, expression) VALUES (?,?,?,?,?)",
            (now, session_id, role, content, expression),
        )
        self._conn.commit()

    def save_session_summary(self, session_id: str, summary: str, topics: list[str] = None):
        self._conn.execute(
            "INSERT OR REPLACE INTO session_summaries (session_id, timestamp, summary, key_topics) VALUES (?,?,?,?)",
            (session_id, datetime.now().isoformat(), summary, json.dumps(topics or [], ensure_ascii=False)),
        )
        self._conn.commit()

    def save_fact(self, fact: str, category: str = "general", session_id: str = None):
        self._conn.execute(
            """INSERT INTO user_facts (fact, category, source_session, created_at)
               VALUES (?,?,?,?) ON CONFLICT(fact) DO NOTHING""",
            (fact, category, session_id, datetime.now().isoformat()),
        )
        self._conn.commit()

    def embed_content(self, content: str, content_type: str, session_id: str = None):
        """Store content in ChromaDB for semantic retrieval."""
        if not self._use_chromadb or not self._chroma:
            return
        content_hash = hashlib.md5(content.encode()).hexdigest()
        exists = self._conn.execute(
            "SELECT id FROM memory_embeddings WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if exists:
            return
        try:
            self._chroma.add(
                documents=[content],
                metadatas=[{"type": content_type, "session": session_id or ""}],
                ids=[f"mem_{content_hash}"],
            )
            self._conn.execute(
                "INSERT INTO memory_embeddings (chroma_id, content_hash, content, content_type, source_session, created_at) VALUES (?,?,?,?,?,?)",
                (f"mem_{content_hash}", content_hash, content, content_type, session_id, datetime.now().isoformat()),
            )
            self._conn.commit()
        except Exception as e:
            log.warning(f"ChromaDB insert failed: {e}")

    # ========== Read ==========

    def get_recent_turns(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM conversations ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_recent_sessions(self, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM session_summaries ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_facts(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM user_facts ORDER BY recall_count DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 5) -> list[str]:
        """Semantic search (ChromaDB) with keyword fallback."""
        if self._use_chromadb and self._chroma:
            try:
                results = self._chroma.query(query_texts=[query], n_results=limit)
                docs = results.get("documents", [[]])[0]
                if docs:
                    return docs
            except Exception as e:
                log.warning(f"ChromaDB query failed: {e}")

        # Keyword fallback
        keywords = [kw for kw in query if len(kw) >= 2]
        if not keywords:
            return []
        conditions = " OR ".join(["content LIKE ?" for _ in keywords[:5]])
        params = [f"%{kw}%" for kw in keywords[:5]]
        rows = self._conn.execute(
            f"SELECT DISTINCT content FROM conversations WHERE {conditions} ORDER BY timestamp DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [r["content"] for r in rows]

    def recall(self, current_text: str, max_items: int = 3) -> str:
        """Build a memory context block for injection into the LLM prompt."""
        parts = []

        memories = self.search(current_text, limit=max_items)
        if memories:
            parts.append("【相关历史对话】")
            for i, m in enumerate(memories, 1):
                parts.append(f"{i}. {m}")

        sessions = self.get_recent_sessions(limit=3)
        if sessions:
            parts.append("【近期会话摘要】")
            for s in sessions[:2]:
                parts.append(f"- {s['summary']}")

        facts = self.get_facts(limit=10)
        if facts:
            parts.append("【已知用户信息】")
            for f in facts:
                parts.append(f"- {f['fact']}")

        return "\n".join(parts) if parts else ""

    # ========== Fact extraction ==========

    def extract_facts(self, text: str, session_id: str) -> list[tuple[str, str]]:
        """Extract user facts from text using pattern matching."""
        found = []
        for pattern, category in FACT_PATTERNS:
            for match in re.finditer(pattern, text):
                fact = match.group(1).strip()
                if len(fact) >= 2 and len(fact) <= 40:
                    found.append((fact, category))
        return found

    # ========== Maintenance ==========

    def mark_facts_recalled(self, facts: list[str]):
        for fact in facts:
            self._conn.execute(
                "UPDATE user_facts SET last_recalled = ?, recall_count = recall_count + 1 WHERE fact = ?",
                (datetime.now().isoformat(), fact),
            )
        self._conn.commit()

    def new_session_id(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]

    def close(self):
        self._conn.close()
        log.info("Memory store closed")
