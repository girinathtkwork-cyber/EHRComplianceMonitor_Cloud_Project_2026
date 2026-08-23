"""
MTSamples Cleaning Script
--------------------------
Reads the raw MTSamples CSV, cleans it, and selects a working subset
for the Intelligent Medical Transcription Verification System project.

Cleaning steps:
1. Drop rows where 'transcription' is null (can't verify empty text)
2. Drop unused columns ('Unnamed: 0' index artifact, 'keywords' - not needed)
3. Select a representative subset across 6 specialties for manageable scope
4. Assign a clean transcript_id to each row
5. Save as JSON for use in later pipeline steps

Run with: python clean_mtsamples.py
Requires: pip install pandas
"""

import pandas as pd
import json

# ---------- Config ----------
INPUT_CSV = "dataset/raw/mtsamples.csv"
OUTPUT_JSON = "dataset/processed/mtsamples_clean.json"

# Chosen for good sample size + diversity, per real distribution found in the dataset
TARGET_SPECIALTIES = [
    "Surgery",
    "Cardiovascular / Pulmonary",
    "Orthopedic",
    "Radiology",
    "General Medicine",
    "Gastroenterology",
]

SAMPLES_PER_SPECIALTY = 45  # ~270 total working samples

# ---------- Load ----------
df = pd.read_csv(INPUT_CSV)
print(f"Raw dataset shape: {df.shape}")

# ---------- Clean ----------
# Step 1: Drop rows with no transcription text
before = len(df)
df = df.dropna(subset=["transcription"])
print(f"Dropped {before - len(df)} rows with missing transcription")

# Step 2: Drop unused columns (ignore errors in case they're already absent)
df = df.drop(columns=["Unnamed: 0", "keywords"], errors="ignore")

# Step 2b: Strip stray whitespace from specialty names
# (found during testing: values like " Surgery" instead of "Surgery")
df["medical_specialty"] = df["medical_specialty"].str.strip()

# Step 3: Filter to our chosen specialties, then sample a fixed number from each
subset_frames = []
for specialty in TARGET_SPECIALTIES:
    specialty_df = df[df["medical_specialty"].str.strip() == specialty]
    n = min(SAMPLES_PER_SPECIALTY, len(specialty_df))
    sampled = specialty_df.sample(n=n, random_state=42)  # fixed seed = reproducible
    subset_frames.append(sampled)
    print(f"{specialty}: selected {n} of {len(specialty_df)} available")

clean_df = pd.concat(subset_frames, ignore_index=True)

# Step 4: Assign a clean, readable transcript_id
clean_df = clean_df.reset_index(drop=True)
clean_df["transcript_id"] = ["mt_" + str(i).zfill(5) for i in range(len(clean_df))]

# Reorder columns for readability
clean_df = clean_df[["transcript_id", "medical_specialty", "sample_name", "description", "transcription"]]

# ---------- Save ----------
records = clean_df.to_dict(orient="records")

import os
os.makedirs("dataset/processed", exist_ok=True)
with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(records, f, indent=2)

print(f"\nSaved {len(records)} cleaned records to {OUTPUT_JSON}")
print(f"Specialties included: {clean_df['medical_specialty'].unique().tolist()}")