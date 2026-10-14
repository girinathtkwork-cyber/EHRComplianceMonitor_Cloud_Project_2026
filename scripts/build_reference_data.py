import os
import json
import time
import math
import re
import urllib.request
import urllib.error
from collections import defaultdict, Counter

RXNORM_URL = "https://rxnav.nlm.nih.gov/REST/allconcepts.json?tty=IN+PIN+BN"
ICD10_RAW_PATH = "dataset/raw/Code Descriptions/icd10cm_order_2027.txt"

OUT_DIR = "dataset/reference"
RXNORM_OUT = os.path.join(OUT_DIR, "rxnorm_lexicon.json")
ICD10_OUT = os.path.join(OUT_DIR, "icd10cm_codes.json")
INDEX_OUT = os.path.join(OUT_DIR, "icd10cm_index.json")

STOPWORDS = {
    'the', 'a', 'an', 'of', 'to', 'in', 'for', 'with', 'and', 'or', 'by', 'is', 
    'on', 'at', 'as', 'from', 'this', 'that', 'other', 'not', 'due', 'without', 
    'unspecified', 'specified', 'nos', 'nec', 'etc'
}

def fetch_rxnorm_with_retry(url, retries=3):
    delay = 2
    for attempt in range(retries + 1):
        try:
            print(f"Fetching RxNorm data (attempt {attempt + 1})...")
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.URLError as e:
            print(f"Error fetching: {e}")
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                raise

def build_rxnorm():
    data = fetch_rxnorm_with_retry(RXNORM_URL)
    
    # Priority: IN > PIN > BN
    priority = {'IN': 1, 'PIN': 2, 'BN': 3}
    lexicon = {}
    
    group = data.get('minConceptGroup', {}).get('minConcept', [])
    for concept in group:
        tty = concept.get('tty')
        name = concept.get('name', '').lower()
        rxcui = concept.get('rxcui')
        
        if not name or tty not in priority:
            continue
            
        if name in lexicon:
            existing_tty = lexicon[name]['tty']
            if priority[tty] < priority[existing_tty]:
                lexicon[name] = {"rxcui": rxcui, "tty": tty}
        else:
            lexicon[name] = {"rxcui": rxcui, "tty": tty}
            
    with open(RXNORM_OUT, 'w', encoding='utf-8') as f:
        json.dump(lexicon, f, indent=2)
        
    print(f"RxNorm Concepts saved: {len(lexicon)}")
    return RXNORM_OUT

def build_icd10():
    codes = {}
    with open(ICD10_RAW_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if len(line) < 77:
                continue
            order = line[0:5].strip()
            code = line[6:13].strip()
            flag = line[14]
            short_desc = line[16:76].strip()
            long_desc = line[77:].strip()
            
            billable = (flag == '1')
            codes[code] = {
                "short": short_desc,
                "long": long_desc,
                "billable": billable
            }
            
    with open(ICD10_OUT, 'w', encoding='utf-8') as f:
        json.dump(codes, f, indent=2)
        
    print(f"ICD-10-CM Codes saved: {len(codes)}")
    return codes, ICD10_OUT

def normalize_text(text):
    text = text.lower()
    tokens = re.split(r'[^a-z]+', text)
    normalized = []
    for t in tokens:
        if not t or t in STOPWORDS:
            continue
        if t.endswith('es'):
            t = t[:-2]
        elif t.endswith('s') and not t.endswith('ss'):
            t = t[:-1]
        if t and t not in STOPWORDS:
            normalized.append(t)
    return normalized

def build_inverted_index(codes_dict):
    N = 0
    df = Counter()
    doc_tokens = {}
    
    # 1. Collect tokens and term frequencies per document
    for code, data in codes_dict.items():
        if not data['billable']:
            continue
        tokens = normalize_text(data['long'])
        if not tokens:
            continue
        N += 1
        tf = Counter(tokens)
        doc_tokens[code] = tf
        for term in tf.keys():
            df[term] += 1
            
    # 2. Compute IDF
    idf = {}
    for term, count in df.items():
        idf[term] = math.log(N / count)
        
    # 3. Build Postings
    postings = defaultdict(list)
    codes_mapping = {}
    for code, tf in doc_tokens.items():
        codes_mapping[code] = codes_dict[code]['long']
        for term, freq in tf.items():
            postings[term].append([code, freq])
            
    index_data = {
        "idf": idf,
        "postings": postings,
        "codes": codes_mapping
    }
    
    with open(INDEX_OUT, 'w', encoding='utf-8') as f:
        json.dump(index_data, f)
        
    print(f"Index built over {N} billable codes. Terms in index: {len(idf)}")
    return index_data, INDEX_OUT

def search_index(index_data, phrase, k=3):
    idf = index_data['idf']
    postings = index_data['postings']
    codes_mapping = index_data['codes']
    
    query_tokens = normalize_text(phrase)
    if not query_tokens:
        return []
        
    query_tf = Counter(query_tokens)
    query_vector = {}
    query_norm_sq = 0.0
    for term, count in query_tf.items():
        if term in idf:
            weight = count * idf[term]
            query_vector[term] = weight
            query_norm_sq += weight * weight
            
    if query_norm_sq == 0:
        return []
        
    scores = defaultdict(float)
    doc_norms_sq = defaultdict(float)
    
    for term, qw in query_vector.items():
        if term in postings:
            for code, tf in postings[term]:
                dw = tf * idf[term]
                scores[code] += qw * dw
                
    candidate_codes = scores.keys()
    for code in candidate_codes:
        doc_long = codes_mapping[code]
        doc_tf = Counter(normalize_text(doc_long))
        norm_sq = 0.0
        for t, freq in doc_tf.items():
            if t in idf:
                norm_sq += (freq * idf[t]) ** 2
        doc_norms_sq[code] = norm_sq
        
    results = []
    for code, dot_product in scores.items():
        q_norm = math.sqrt(query_norm_sq)
        d_norm = math.sqrt(doc_norms_sq[code]) if doc_norms_sq[code] > 0 else 1.0
        sim = dot_product / (q_norm * d_norm)
        results.append((sim, code, codes_mapping[code]))
        
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:k]

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    
    rx_out = build_rxnorm()
    codes, icd_out = build_icd10()
    index_data, idx_out = build_inverted_index(codes)
    
    print("\nSearch Test:")
    for phrase in ['type 2 diabetes mellitus', 'chronic otitis media with effusion']:
        print(f"Query: '{phrase}'")
        res = search_index(index_data, phrase, k=3)
        for sim, code, desc in res:
            print(f"  {code} ({sim:.4f}) - {desc}")
            
    print("\nFile Sizes:")
    total_size = 0
    for path in [rx_out, icd_out, idx_out]:
        sz = os.path.getsize(path)
        total_size += sz
        print(f"{os.path.basename(path)}: {sz / (1024*1024):.2f} MB")
        
    print(f"Total Size: {total_size / (1024*1024):.2f} MB")
    if total_size > 45 * 1024 * 1024:
        print("Note: Combined size > 45MB. S3-at-cold-start is recommended.")

if __name__ == '__main__':
    main()
