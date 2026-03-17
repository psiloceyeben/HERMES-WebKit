"""
Holographic Reduced Representations (HRR) memory engine for HERMES vessels.
Encodes facts as superposed complex-valued vectors via circular convolution.
Sub-millisecond recall, fixed-size storage, no external APIs needed.
"""

import hashlib
import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("hermes.hrr")

# Default vector dimensionality — higher = more capacity, 1024 is plenty
DIM = 1024


def _seed_vector(label: str, dim: int = DIM) -> np.ndarray:
    """Generate a deterministic unit-length complex vector from a string seed.
    Same label always produces the same vector — enables reconstruction from seeds."""
    h = int(hashlib.sha256(label.encode()).hexdigest(), 16)
    rng = np.random.RandomState(h % (2**31))
    phases = rng.uniform(0, 2 * np.pi, dim)
    return np.exp(1j * phases) / np.sqrt(dim)


def _circular_conv(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Circular convolution in frequency domain — the binding operation."""
    return np.fft.ifft(np.fft.fft(a) * np.fft.fft(b))


def _circular_corr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Circular correlation — the unbinding (recall) operation."""
    return np.fft.ifft(np.conj(np.fft.fft(a)) * np.fft.fft(b))


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two complex vectors."""
    return float(np.abs(np.dot(a.conj(), b)) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


class HolographicMemory:
    """A single holographic memory store — one per vessel."""

    def __init__(self, path: str = None, dim: int = DIM):
        self.dim = dim
        self.path = Path(path) if path else None
        # The superposition vector — all facts encoded here
        self.memory = np.zeros(dim, dtype=complex)
        # Index of bound keys (for rebuilding and listing)
        self.index: list[dict] = []  # [{key, value, seed, recall_count, ts}]
        self.recall_counts: dict[str, int] = {}
        # Load if exists
        if self.path and self.path.exists():
            self._load()

    def _load(self):
        """Load index from disk and rebuild memory vector from seeds."""
        try:
            data = json.loads(self.path.read_text())
            self.index = data.get("index", [])
            self.recall_counts = data.get("recall_counts", {})
            # Rebuild the memory vector from stored facts
            self.memory = np.zeros(self.dim, dtype=complex)
            for entry in self.index:
                key_vec = _seed_vector(entry["key"], self.dim)
                val_vec = _seed_vector(entry["value"], self.dim)
                self.memory += _circular_conv(key_vec, val_vec)
            log.info(f"HRR loaded {len(self.index)} facts from {self.path}")
        except Exception as e:
            log.warning(f"HRR load error: {e}")
            self.memory = np.zeros(self.dim, dtype=complex)
            self.index = []

    def _save(self):
        """Save index to disk. Vector is rebuilt from seeds on load."""
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "dim": self.dim,
                "index": self.index,
                "recall_counts": self.recall_counts,
            }
            self.path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            log.warning(f"HRR save error: {e}")

    def bind(self, key: str, value: str, metadata: dict = None):
        """Encode a key-value fact into the holographic memory."""
        key_vec = _seed_vector(key, self.dim)
        val_vec = _seed_vector(value, self.dim)
        self.memory += _circular_conv(key_vec, val_vec)
        entry = {"key": key, "value": value[:500]}
        if metadata:
            entry["meta"] = metadata
        self.index.append(entry)
        self._save()

    def recall(self, key: str) -> list[tuple[str, float]]:
        """Recall values associated with a key. Returns [(value, similarity)] sorted by relevance."""
        if np.linalg.norm(self.memory) < 1e-10:
            return []
        key_vec = _seed_vector(key, self.dim)
        retrieved = _circular_corr(key_vec, self.memory)
        # Check against all known values
        results = []
        for entry in self.index:
            val_vec = _seed_vector(entry["value"], self.dim)
            sim = _cosine_sim(retrieved, val_vec)
            if sim > 0.05:  # threshold for noise
                results.append((entry["value"], sim, entry.get("meta", {})))
        # Track recall counts
        self.recall_counts[key] = self.recall_counts.get(key, 0) + 1
        self._save()
        return sorted(results, key=lambda x: x[1], reverse=True)[:10]

    def forget(self, key: str, value: str):
        """Remove a specific binding from the superposition."""
        key_vec = _seed_vector(key, self.dim)
        val_vec = _seed_vector(value, self.dim)
        self.memory -= _circular_conv(key_vec, val_vec)
        self.index = [e for e in self.index if not (e["key"] == key and e["value"] == value)]
        self._save()

    def novelty(self, text: str) -> float:
        """Check how novel a piece of text is relative to existing memory.
        Returns 0.0 (completely redundant) to 1.0 (completely new).
        This replaces the Hecate API call for similarity classification.

        Uses keyword extraction + HRR binding overlap rather than raw text hashing,
        because seed vectors are hash-based (not semantic embeddings)."""
        if not self.index:
            return 1.0  # empty memory = everything is novel

        import re
        # Extract meaningful words from the input
        stop = {"the","a","an","is","are","was","were","be","been","being","have","has",
                "had","do","does","did","will","would","could","should","may","might",
                "shall","can","to","of","in","for","on","with","at","by","from","as",
                "into","about","like","through","after","over","between","out","up",
                "i","you","we","they","he","she","it","me","my","your","our","this",
                "that","these","those","what","which","who","how","when","where","why",
                "not","no","yes","just","also","very","so","and","but","or","if","then",
                "than","too","its","im","dont","thats","ive","lets","want","need","think"}
        words = set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()) - stop
        if not words:
            return 0.0  # nothing meaningful = skip

        # Check keyword overlap against stored facts
        max_overlap = 0.0
        for entry in self.index:
            fact_words = set(re.sub(r"[^a-z0-9 ]", " ",
                (entry.get("key","") + " " + entry.get("value","")).lower()).split()) - stop
            if not fact_words:
                continue
            overlap = len(words & fact_words)
            union = len(words | fact_words)
            jaccard = overlap / union if union > 0 else 0
            if jaccard > max_overlap:
                max_overlap = jaccard

        # Also use HRR: bind all input keywords into a query vector and check
        query_vec = np.zeros(self.dim, dtype=complex)
        for w in list(words)[:20]:
            query_vec += _seed_vector(w, self.dim)
        if np.linalg.norm(query_vec) > 1e-10:
            hrr_sim = _cosine_sim(query_vec / np.linalg.norm(query_vec), self.memory)
        else:
            hrr_sim = 0.0

        # Combine keyword overlap (weighted heavier) and HRR similarity
        combined = max_overlap * 0.7 + float(hrr_sim) * 0.3
        return float(max(0.0, min(1.0, 1.0 - combined)))

    def get_hot_facts(self, threshold: int = 3) -> list[dict]:
        """Get facts recalled more than threshold times — candidates for promotion."""
        hot = []
        for key, count in self.recall_counts.items():
            if count >= threshold:
                hot.append({"key": key, "recall_count": count})
        return hot

    def stats(self) -> dict:
        """Return memory statistics."""
        return {
            "total_facts": len(self.index),
            "vector_norm": float(np.linalg.norm(self.memory)),
            "hot_facts": len(self.get_hot_facts()),
            "unique_keys": len(set(e["key"] for e in self.index)),
        }
