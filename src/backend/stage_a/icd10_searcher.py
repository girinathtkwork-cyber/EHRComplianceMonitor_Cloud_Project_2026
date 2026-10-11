import json
import math
import re
from collections import Counter
from pathlib import Path

STOPWORDS = {
    'the', 'a', 'an', 'of', 'to', 'in', 'for', 'with', 'and', 'or', 'by', 
    'is', 'on', 'at', 'as', 'from', 'this', 'that', 'other', 'not', 'due', 
    'without', 'unspecified', 'specified', 'nos', 'nec'
}

class ICD10Searcher:
    def __init__(self, index_path=None):
        if index_path is None:
            # Find project root by walking up from this file
            current_dir = Path(__file__).resolve().parent
            project_root = current_dir.parent.parent.parent
            index_path = project_root / 'dataset' / 'reference' / 'icd10cm_index.json'
            
        with open(index_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.idf = data.get('idf', {})
            self.postings = data.get('postings', {})
            self.codes = data.get('codes', {})
            
        # Precompute document norms for cosine similarity
        self.doc_norms = {}
        for term, postings in self.postings.items():
            idf = self.idf.get(term, 0.0)
            for code, tf in postings:
                weight = tf * idf
                self.doc_norms[code] = self.doc_norms.get(code, 0.0) + weight * weight
                
        for code in self.doc_norms:
            self.doc_norms[code] = math.sqrt(self.doc_norms[code])
            
    def _normalize(self, text: str) -> list[str]:
        """
        Normalize text: lowercase, tokenize on non-alpha/numeric, drop stopwords, 
        and perform simple plural stripping.
        """
        tokens = re.split(r'[^a-z0-9]+', text.lower())
        normalized = []
        for token in tokens:
            if not token or token in STOPWORDS:
                continue
            
            if token.endswith('sses'):
                pass
            elif token.endswith('ies') and len(token) > 4:
                token = token[:-3] + 'y'
            elif token.endswith('es') and len(token) > 3:
                token = token[:-2]
            elif token.endswith('s') and not token.endswith('ss'):
                token = token[:-1]
                
            normalized.append(token)
        return normalized

    def search(self, phrase: str, k: int = 3) -> list[dict]:
        """
        Search for ICD-10 codes matching the given phrase.
        Returns top k matches sorted by cosine similarity score.
        """
        tokens = self._normalize(phrase)
        if not tokens:
            return []
            
        term_counts = Counter(tokens)
        query_tfidf = {}
        query_norm = 0.0
        
        for term, count in term_counts.items():
            if term in self.idf:
                tf = count / len(tokens)
                weight = tf * self.idf[term]
                query_tfidf[term] = weight
                query_norm += weight * weight
                
        if query_norm == 0.0:
            return []
            
        query_norm = math.sqrt(query_norm)
        
        # Calculate dot product
        scores = {}
        for term, q_weight in query_tfidf.items():
            postings = self.postings.get(term, [])
            for code, tf in postings:
                d_weight = tf * self.idf[term]
                scores[code] = scores.get(code, 0.0) + (q_weight * d_weight)
                
        # Normalize to cosine similarity (0 to 1)
        results = []
        for code, dot_product in scores.items():
            d_norm = self.doc_norms.get(code, 1.0)
            if d_norm > 0:
                score = dot_product / (query_norm * d_norm)
                # Ensure score is within 0-1 bounds due to float precision
                score = min(max(score, 0.0), 1.0)
                results.append((score, code))
                
        # Sort by score descending
        results.sort(key=lambda x: x[0], reverse=True)
        
        # Format output
        top_k = []
        for score, code in results[:k]:
            top_k.append({
                "code": code,
                "description": self.codes.get(code, ""),
                "score": round(score, 4)
            })
            
        return top_k

if __name__ == "__main__":
    import sys
    
    # Simple test cases
    try:
        searcher = ICD10Searcher()
        
        test_queries = [
            "type 2 diabetes mellitus",
            "acute appendicitis",
            "essential hypertension"
        ]
        
        for query in test_queries:
            print(f"\nQuery: '{query}'")
            results = searcher.search(query)
            for i, res in enumerate(results):
                print(f"  {i+1}. {res['code']} ({res['score']}): {res['description']}")
                
    except FileNotFoundError as e:
        print(f"Skipping tests: {e}")
