from pathlib import Path

from config import load_config
from extractor import extract_pdf


Z = [0] * 12
EXPECTED = {
    "234645-000039": (Z, "정상"),
    "234645-000733": ([0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0], "정상"),
    "234645-000467": (Z, "정상"),
    "234645-000093": (Z, "확인필요"),
    "231087-000452": (Z, "확인필요"),
    "231087-000514": (Z, "정상"),
    "231087-000457": ([3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], "정상"),
    "231087-000416": (Z, "확인필요"),
    "231087-000371": ([0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0], "정상"),
    "231087-000233": (Z, "정상"),
    "231087-000352": (Z, "확인필요"),
    "231087-000322": ([0, 0, 0, 0, 16, 0, 0, 1, 0, 0, 11, 0], "정상"),
    "231087-000183": ([0, 0, 0, 2, 0, 0, 1, 0, 0, 0, 0, 0], "정상"),
    "231087-000095": ([5, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0], "정상"),
    "231087-000112": (Z, "정상"),
    "231087-000182": (Z, "정상"),
    "231087-000052": ([0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0], "정상"),
    "231087-000100": ([0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0], "정상"),
}


def main():
    cfg = load_config()
    pdfs = sorted(Path("../upload").glob("*.pdf"))
    tested = failed = 0
    for pdf in pdfs:
        result = extract_pdf(pdf, cfg, Path("../tmp/test_diagnostics"))
        if result.applicant_id not in EXPECTED:
            continue
        values, status = EXPECTED[result.applicant_id]
        ok = result.values == values and result.status == status
        print("PASS" if ok else "FAIL", result.applicant_id, result.values, result.status, result.detail)
        tested += 1
        failed += not ok
    assert tested, "검증 가능한 샘플 PDF를 찾지 못했습니다."
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
