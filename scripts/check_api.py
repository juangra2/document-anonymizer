"""Quick API smoke test — run after the server starts on port 8765."""
import httpx
import sys

BASE = "http://127.0.0.1:8765"

# 1. Health
r = httpx.get(f"{BASE}/health", timeout=5)
assert r.status_code == 200, f"health failed: {r.status_code}"
print(f"GET /health        -> {r.status_code}  {r.json()}")

# 2. Anonymize sample PDF
pdf_path = "samples/sample_pii.pdf"
with open(pdf_path, "rb") as f:
    r2 = httpx.post(
        f"{BASE}/anonymize",
        params={"output": "zip", "mode": "strict"},
        files={"file": ("sample_pii.pdf", f, "application/pdf")},
        timeout=90,
    )

h = r2.headers
print(f"POST /anonymize    → {r2.status_code}")
print(f"  Content-Type          : {h.get('content-type')}")
print(f"  X-Anonymization-Status: {h.get('x-anonymization-status')}")
print(f"  X-Entities-Detected   : {h.get('x-entities-detected')}")
print(f"  X-Entities-Redacted   : {h.get('x-entities-redacted')}")
print(f"  X-Residual-Entities   : {h.get('x-residual-entities')}")
print(f"  X-Manual-Review       : {h.get('x-manual-review-required')}")
print(f"  X-Redaction-Mode      : {h.get('x-redaction-mode')}")
print(f"  Response bytes        : {len(r2.content)}")

if r2.status_code != 200:
    print(f"\nERROR BODY:\n{r2.text[:800]}")
    sys.exit(1)

# 3. Anonymize complex PDF
pdf2 = "samples/sample_complex_pii.pdf"
with open(pdf2, "rb") as f:
    r3 = httpx.post(
        f"{BASE}/anonymize",
        params={"output": "pdf", "mode": "strict"},
        files={"file": ("sample_complex_pii.pdf", f, "application/pdf")},
        timeout=90,
    )

print(f"\nPOST /anonymize (complex) → {r3.status_code}")
print(f"  X-Anonymization-Status: {r3.headers.get('x-anonymization-status')}")
print(f"  X-Entities-Detected   : {r3.headers.get('x-entities-detected')}")
print(f"  X-Entities-Redacted   : {r3.headers.get('x-entities-redacted')}")

if r3.status_code != 200:
    print(f"\nERROR BODY:\n{r3.text[:800]}")
    sys.exit(1)

print("\nAll API checks passed.")
