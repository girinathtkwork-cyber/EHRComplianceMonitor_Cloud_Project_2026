import os
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False


@dataclass
class LLMReasoningResult:
    is_plausible: bool
    confidence: float
    explanation: str
    flagged_issues: List[str]


STAGE_B_PROMPT = """You are a clinical pharmacist reviewing medical transcription output for errors.

CONTEXT:
- Transcript: {transcript_text}
- Entities extracted: {entities_json}
- Flagged entities from Stage A: {flagged_json}

TASK:
For EACH flagged entity, determine if it represents a genuine medical error/implausibility or if Stage A was over-cautious.

Analyze each flag in context:
1. POTENTIAL_HALLUCINATION: Is the medication/procedure/diagnosis medically plausible for this patient? Consider drug-disease contraindications, standard of care.
2. DOSAGE_ANOMALY: Is the dose within safe/standard range for this medication, patient condition, renal/hepatic function?
3. AMBIGUOUS_TERM: Can the term be reasonably disambiguated from context? Is the dosing regimen clear?
4. UNRECOGNIZED_CODE: Is this a known term with a standard code that was missed?
5. MISSING_CODE: Is this a high-risk substance (herbal, supplement) with interaction risk?

OUTPUT FORMAT (JSON only):
{{
  "reasoning_per_flag": [
    {{
      "entity_id": "string",
      "flag_type": "string",
      "is_plausible": true/false,
      "confidence": 0.0-1.0,
      "explanation": "Clinical reasoning for determination",
      "issues": ["specific issue 1", "specific issue 2"]
    }}
  ],
  "overall_assessment": "Summary of transcription quality and major concerns"
}}"""


def configure_gemini(api_key: Optional[str] = None) -> bool:
    if not GENAI_AVAILABLE:
        print("google-generativeai not installed. Run: pip install google-generativeai")
        return False
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY not set. Set env var or pass api_key.")
        return False
    genai.configure(api_key=key)
    return True


def build_prompt(stage_a_data: Dict[str, Any]) -> str:
    entities_json = json.dumps(stage_a_data.get("entities", []), indent=2)
    flagged_json = json.dumps(stage_a_data.get("flagged_entities", []), indent=2)
    transcript = stage_a_data.get("transcript_text", "")
    
    return STAGE_B_PROMPT.format(
        transcript_text=transcript,
        entities_json=entities_json,
        flagged_json=flagged_json
    )


def call_gemini(prompt: str, model_name: str = "gemini-1.5-flash") -> Optional[str]:
    if not GENAI_AVAILABLE:
        return None
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,
                max_output_tokens=2048,
                response_mime_type="application/json"
            )
        )
        return response.text
    except Exception as e:
        print(f"Gemini API error: {e}")
        return None


def parse_llm_response(response_text: str) -> Optional[List[Dict]]:
    try:
        data = json.loads(response_text)
        return data.get("reasoning_per_flag", [])
    except json.JSONDecodeError as e:
        print(f"Failed to parse LLM response: {e}")
        print(f"Raw response: {response_text[:500]}")
        return None


def run_llm_reasoning(stage_a_data: Dict[str, Any]) -> List[Dict]:
    if not configure_gemini():
        return []
    
    prompt = build_prompt(stage_a_data)
    response = call_gemini(prompt)
    
    if not response:
        return []
    
    return parse_llm_response(response) or []


def combine_with_severity(
    stage_a_data: Dict[str, Any],
    severity_result: Dict[str, str],
    llm_results: List[Dict]
) -> Dict[str, Any]:
    reasoning_map = {r["entity_id"]: r for r in llm_results}
    
    combined_flags = []
    for flag in stage_a_data.get("flagged_entities", []):
        eid = flag["entity_id"]
        llm = reasoning_map.get(eid, {})
        
        combined_flags.append({
            **flag,
            "llm_plausible": llm.get("is_plausible"),
            "llm_confidence": llm.get("confidence"),
            "llm_explanation": llm.get("explanation"),
            "llm_issues": llm.get("issues", []),
            "final_severity": severity_result["severity"],
            "severity_reason": severity_result["reason"]
        })
    
    return {
        "transcript_id": stage_a_data["transcript_id"],
        "timestamp": stage_a_data["timestamp"],
        "transcript_text": stage_a_data["transcript_text"],
        "entities": stage_a_data["entities"],
        "flagged_entities": combined_flags,
        "overall_severity": severity_result["severity"],
        "severity_reason": severity_result["reason"],
        "llm_overall_assessment": llm_results[0].get("overall_assessment", "") if llm_results else "",
        "processed_at": datetime.utcnow().isoformat() + "Z"
    }


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python llm_reasoning.py <sample_json_path>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    with open(filepath, 'r') as f:
        stage_a = json.load(f)
    
    print("Running LLM reasoning...")
    results = run_llm_reasoning(stage_a)
    
    print(f"\nLLM Results ({len(results)} flags analyzed):")
    for r in results:
        print(f"  {r['entity_id']} ({r['flag_type']}): plausible={r['is_plausible']} conf={r['confidence']:.2f}")
        print(f"    {r['explanation'][:150]}...")
        if r['issues']:
            print(f"    Issues: {', '.join(r['issues'])}")