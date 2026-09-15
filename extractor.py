from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz
from PIL import Image, ImageOps


@dataclass
class ExtractionResult:
    applicant_id: str
    values: list[int]
    status: str
    detail: str
    filename: str
    diagnostic_path: str = ""
    evidence: str = ""


def _clusters(values: list[int], gap: int = 2) -> list[int]:
    if not values:
        return []
    groups = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group) / len(group)) for group in groups]


def _longest_dark_run(image: Image.Image, y: int, threshold: int = 185) -> int:
    px = image.load()
    best = current = 0
    for x in range(image.width):
        if px[x, y] < threshold:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _horizontal_line_score(image: Image.Image) -> int:
    """Score long horizontal rules; used to deskew photographed/scanned pages."""
    sample = image.copy()
    sample.thumbnail((800, 1200))
    gray = ImageOps.grayscale(sample)
    values = [
        _longest_dark_run(gray, y, threshold=195)
        for y in range(int(gray.height * .38), int(gray.height * .92), 2)
    ]
    return sum(sorted(values, reverse=True)[:12])


def _deskew(image: Image.Image) -> tuple[Image.Image, float]:
    candidates = []
    for angle in (-3, -2.5, -2, -1.5, -1, -.5, 0, .5, 1, 1.5, 2, 2.5, 3):
        preview = image if angle == 0 else image.rotate(
            angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor="white"
        )
        candidates.append((_horizontal_line_score(preview), angle))
    _, angle = max(candidates)
    if angle == 0:
        return image, 0.0
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white"), angle


def _find_attendance_rows(image: Image.Image) -> list[tuple[int, list[int], int, int]]:
    """Find attendance data rows, including headerless rows continued on page 2."""
    gray = ImageOps.grayscale(image)
    w, h = gray.size
    px = gray.load()
    full_lines = _clusters([
        y for y in range(int(h * .03), int(h * .94))
        if _longest_dark_run(gray, y, threshold=205) > w * .62
    ], gap=3)
    candidates = []
    for y1, y2 in zip(full_lines, full_lines[1:]):
        if not (h * .012 <= y2 - y1 <= h * .038):
            continue
        vertical = []
        for x in range(w):
            best_run = current = 0
            for y in range(y1, y2 + 1):
                if px[x, y] < 205:
                    current += 1
                    best_run = max(best_run, current)
                else:
                    current = 0
            if best_run > (y2 - y1) * .65:
                vertical.append(x)
        all_xs = [x for x in _clusters(vertical, gap=4) if w * .02 < x < w * .98]
        best_xs = None
        for start in range(max(1, len(all_xs) - 14)):
            core = all_xs[start:start + 15]
            if len(core) < 15:
                continue
            widths = [b - a for a, b in zip(core, core[1:])]
            metric = widths[2:14]
            if min(metric) <= 0 or max(metric) / min(metric) > 2.8:
                continue
            typical = sorted(metric)[len(metric) // 2]
            ends = [x for x in all_xs[start + 15:] if x - core[-1] > typical * 2]
            if not ends:
                continue
            run = core + [ends[0]]
            if run[-1] - run[0] >= w * .62:
                best_xs = run
                break
        if best_xs is None:
            continue
        year_crop = image.crop((best_xs[0] + 2, y1 + 2, best_xs[1] - 2, y2 - 2))
        year, _ = _read_cell(year_crop, psm=10)
        candidates.append((year if year in (1, 2, 3) else 0, best_xs, y1, y2))

    rows = [row for row in candidates if row[0] in (1, 2, 3)]
    # Low-quality grayscale scans can obscure the year digit while their three
    # row boundaries remain clear. Infer 1/2/3 only when exactly three rows are
    # contiguous and any readable year agrees with that order.
    for i in range(len(candidates) - 2):
        group = candidates[i:i + 3]
        contiguous = all(abs(group[j][3] - group[j + 1][2]) <= 5 for j in range(2))
        consistent = all(year in (0, expected) for expected, (year, *_rest) in enumerate(group, 1))
        if contiguous and consistent:
            inferred = [(year, row[1], row[2], row[3]) for year, row in enumerate(group, 1)]
            existing = {(row[0], row[2], row[3]) for row in rows}
            rows.extend(row for row in inferred if (row[0], row[2], row[3]) not in existing)
            break
    for i in range(len(candidates) - 1):
        group = candidates[i:i + 2]
        if abs(group[0][3] - group[1][2]) > 5:
            continue
        years = [group[0][0], group[1][0]]
        expected = None
        if years[0] in (0, 2) and years[1] in (0, 3) and (2 in years or 3 in years):
            expected = (2, 3)
        elif years[0] in (0, 1) and years[1] in (0, 2) and (1 in years or 2 in years):
            expected = (1, 2)
        if expected:
            existing = {(row[0], row[2], row[3]) for row in rows}
            inferred = [(year, row[1], row[2], row[3]) for year, row in zip(expected, group)]
            rows.extend(row for row in inferred if (row[0], row[2], row[3]) not in existing)
    return sorted(rows, key=lambda row: row[2])


def _camera_like(image: Image.Image) -> bool:
    """Detect a page-wide colour cast typical of phone-camera captures."""
    sample = image.copy()
    sample.thumbnail((160, 220))
    pixels = list(sample.convert("RGB").getdata())
    if not pixels:
        return False
    spread = sum(max(p) - min(p) for p in pixels) / len(pixels)
    return spread > 13


def _find_attendance_grid(image: Image.Image) -> tuple[list[int], list[int]]:
    """Return 16 x-boundaries and horizontal grid lines for the attendance table."""
    gray = ImageOps.grayscale(image)
    w, h = gray.size
    horizontal_candidates = _clusters([
        y for y in range(int(h * .40), int(h * .93))
        if _longest_dark_run(gray, y) > w * .66
    ])

    px = gray.load()
    best = None
    for top in horizontal_candidates:
        if top > h * .88:
            continue
        # Attendance grids are shallow and have 16 vertical boundaries.
        bottom_limit = min(h - 1, top + int(h * .11))
        vertical_scores = []
        for x in range(w):
            dark = sum(px[x, y] < 185 for y in range(top, bottom_limit))
            if dark > (bottom_limit - top) * .24:
                vertical_scores.append(x)
        xs = _clusters(vertical_scores, gap=3)
        # Ignore narrow false detections and retain plausible full table spans.
        xs = [x for x in xs if w * .03 < x < w * .97]
        if len(xs) < 15:
            continue
        # Find the best consecutive run of 16 boundaries with table-like spacing.
        for start in range(max(1, len(xs) - 15)):
            run = xs[start:start + 16]
            if len(run) < 16:
                continue
            widths = [b - a for a, b in zip(run, run[1:])]
            if run[-1] - run[0] < w * .65 or min(widths) < w * .018:
                continue
            metric = widths[2:14]
            regularity = max(metric) / max(1, min(metric))
            if regularity > 2.6:
                continue
            # Prefer the lowest matching grid: attendance is the final numbered
            # section on these first pages. Earlier academic tables can otherwise
            # see the attendance grid through the look-ahead window.
            score = (run[-1] - run[0]) - regularity * 100 + top * .25
            if best is None or score > best[0]:
                best = (score, run, top)

    if best is None:
        raise ValueError("출결 표의 세로선을 찾지 못했습니다.")

    _, xs, top = best
    left, right = xs[0], xs[-1]
    full_lines = []
    for y in range(int(h * .40), min(h, int(h * .93))):
        best_run = current = 0
        for x in range(left, right + 1):
            if px[x, y] < 185:
                current += 1
                best_run = max(best_run, current)
            else:
                current = 0
        if best_run > (right - left) * .70:
            full_lines.append(y)
    all_ys = _clusters(full_lines, gap=3)
    # Table pattern: a tall two-level header followed by 1-3 shorter year rows.
    # Select the latest valid sequence so unrelated sections above are excluded.
    sequences = []
    for i in range(len(all_ys) - 2):
        header_height = all_ys[i + 1] - all_ys[i]
        first_row_height = all_ys[i + 2] - all_ys[i + 1]
        if h * .032 <= header_height <= h * .065 and h * .013 <= first_row_height <= h * .035:
            seq = all_ys[i:i + 3]
            for y in all_ys[i + 3:]:
                gap = y - seq[-1]
                if h * .013 <= gap <= h * .035 and len(seq) < 5:
                    seq.append(y)
                else:
                    break
            sequences.append(seq)
    if not sequences:
        raise ValueError("출결 표의 가로선을 찾지 못했습니다.")
    ys = sequences[-1]
    return xs, ys


def _tesseract_command() -> str:
    bundled = Path(getattr(__import__('sys'), '_MEIPASS', '')) / "tesseract" / "tesseract.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which("tesseract")
    if not found:
        raise RuntimeError("OCR 엔진(tesseract)을 찾지 못했습니다.")
    return found


def _read_cell(cell: Image.Image, psm: int = 6) -> tuple[int, str]:
    # Enlarge and remove grid-line remnants before recognizing digits and periods.
    cell = ImageOps.grayscale(cell)
    # A blank or the official record's centered period means zero. Detect this
    # geometrically before OCR because Tesseract can mistake a scan speck for 7.
    ink = cell.point(lambda p: 255 if p < 150 else 0).getbbox()
    if ink is None:
        return 0, "."
    bw, bh = ink[2] - ink[0], ink[3] - ink[1]
    if bw < cell.width * .25 and bh < cell.height * .25:
        return 0, "."
    # Count substantial connected ink components before enlargement. A clean
    # one-digit glyph must not be accepted as a two-digit OCR result (the
    # Windows OCR engine has read a single printed 5 as "50" in production).
    mask = cell.point(lambda p: 255 if p < 180 else 0)
    mp = mask.load()
    seen = set()
    major_components = 0
    min_area = max(3, int(cell.width * cell.height * .008))
    min_height = max(3, int(cell.height * .30))
    for sy in range(cell.height):
        for sx in range(cell.width):
            if not mp[sx, sy] or (sx, sy) in seen:
                continue
            stack = [(sx, sy)]
            seen.add((sx, sy))
            area = 0
            min_y = max_y = sy
            while stack:
                x, y = stack.pop()
                area += 1
                min_y, max_y = min(min_y, y), max(max_y, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < cell.width and 0 <= ny < cell.height and mp[nx, ny] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            if area >= min_area and max_y - min_y + 1 >= min_height:
                major_components += 1

    cell = ImageOps.autocontrast(cell.resize((cell.width * 4, cell.height * 4)))
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "cell.png"
        cell.save(path)
        attempts = (psm,) if psm == 10 else (psm, 13, 10)
        recognized: list[tuple[int, str]] = []
        raw = ""
        for attempt in attempts:
            proc = subprocess.run(
                [_tesseract_command(), str(path), "stdout", "--psm", str(attempt), "-l", "eng",
                 "-c", "tessedit_char_whitelist=0123456789."],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            candidate = proc.stdout.strip().replace(" ", "")
            digits = re.findall(r"\d+", candidate)
            if digits:
                recognized.append((int(digits[0]), candidate))
            raw = raw or candidate
        if recognized:
            values = [value for value, _candidate in recognized]
            multi = [value for value in values if value >= 10]
            if major_components == 1 and multi:
                singles = [value for value in values if value < 10]
                if singles:
                    counts = {value: singles.count(value) for value in set(singles)}
                    chosen = max(counts, key=counts.get)
                    if counts[chosen] > len(values) / 2:
                        return chosen, str(chosen)
                else:
                    chosen = int(str(multi[0])[0])
                return chosen, f"형태보정({multi[0]}→{chosen})"
            if len(set(values)) == 1:
                return values[0], recognized[0][1]
            counts = {value: values.count(value) for value in set(values)}
            best_count = max(counts.values())
            winners = [value for value, count in counts.items() if count == best_count]
            chosen = min(winners, key=lambda value: (len(str(value)), value))
            if best_count > len(values) / 2:
                return chosen, str(chosen)
            return chosen, "OCR불일치(" + "/".join(str(value) for value in values) + ")"
    if raw in {"", ".", "..", "..."}:
        return 0, raw or "."
    return 0, raw


def _extract_row_values(image: Image.Image, xs: list[int], y1: int, y2: int) -> tuple[list[int], list[str]]:
    values = []
    ambiguous = []
    for idx in range(12):
        x1, x2 = xs[idx + 2], xs[idx + 3]
        margin_x = max(2, int((x2 - x1) * .10))
        margin_y = max(2, int((y2 - y1) * .12))
        crop = image.crop((x1 + margin_x, y1 + margin_y, x2 - margin_x, y2 - margin_y))
        value, raw = _read_cell(crop)
        values.append(value)
        if raw not in {"", ".", "..", "..."} and not raw.isdigit():
            ambiguous.append(f"{idx + 1}열='{raw}'")
    return values, ambiguous


def _find_rows_from_full_grid(image: Image.Image) -> list[tuple[int, list[int], int, int]]:
    """Fallback for a page containing only one attendance year row."""
    try:
        xs, ys = _find_attendance_grid(image)
    except ValueError:
        return []
    if len(ys) < 3:
        return []
    rows = []
    for y1, y2 in zip(ys[1:], ys[2:]):
        year_crop = image.crop((xs[0] + 2, y1 + 2, xs[1] - 2, y2 - 2))
        year, _raw = _read_cell(year_crop, psm=10)
        if year in (1, 2, 3):
            rows.append((year, xs, y1, y2))
    return rows


def extract_pdf(pdf_path: Path, cfg: dict, diagnostics_dir: Path | None = None) -> ExtractionResult:
    match = re.search(cfg["applicant_id_pattern"], pdf_path.name)
    applicant_id = match.group(0) if match else ""
    if not applicant_id:
        return ExtractionResult("", [0] * 12, "확인필요", "지원자번호 추출 실패", pdf_path.name)

    diagnostic_path = ""
    try:
        doc = fitz.open(pdf_path)
        scale = cfg["render_dpi"] / 72
        rows_by_year = {}
        pages_by_year = {}
        row_images = {}
        ambiguous = []
        camera_pages = []
        deskewed_pages = []
        page_errors = []
        # Some certified records place a cover/certificate before the detailed
        # record. Check the first three pages and stop as soon as a valid
        # 12-column attendance grid is found.
        for page_index in range(min(3, doc.page_count)):
            page = doc[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            original = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            candidate = original
            page_rows = _find_attendance_rows(candidate)
            if not page_rows:
                page_rows = _find_rows_from_full_grid(candidate)
            angle = 0.0
            if not page_rows:
                candidate, angle = _deskew(original)
                page_rows = _find_attendance_rows(candidate)
                if not page_rows:
                    page_rows = _find_rows_from_full_grid(candidate)
            if not page_rows:
                page_errors.append(f"{page_index + 1}쪽: 출결 데이터 행 미검출")
                continue
            if _camera_like(original):
                camera_pages.append(page_index + 1)
            if angle:
                deskewed_pages.append(page_index + 1)
            for year, row_xs, y1, y2 in page_rows:
                values, row_ambiguous = _extract_row_values(candidate, row_xs, y1, y2)
                if year in rows_by_year and rows_by_year[year] != values:
                    ambiguous.append(f"{year}학년 중복 판독값 불일치")
                else:
                    rows_by_year[year] = values
                    pages_by_year[year] = page_index + 1
                    row_images[year] = candidate.crop((row_xs[0], y1, row_xs[-1], y2))
                ambiguous.extend(f"{year}학년 {item}" for item in row_ambiguous)
            if set(rows_by_year) == {1, 2, 3}:
                break

        totals = [sum(rows_by_year[y][i] for y in rows_by_year) for i in range(12)]

        if diagnostics_dir and cfg.get("save_diagnostic_images"):
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            for year, diagnostic in row_images.items():
                out = diagnostics_dir / f"{applicant_id}_{year}학년.png"
                diagnostic.save(out)
                diagnostic_path = str(out)

        status = "정상"
        detail = ""
        if ambiguous:
            status, detail = "확인필요", "; ".join(ambiguous)
        elif set(rows_by_year) != {1, 2, 3}:
            status, detail = "확인필요", f"판독 학년 불완전: {sorted(rows_by_year) or '없음'}"
        elif camera_pages:
            status, detail = "확인필요", f"촬영본 추정: {camera_pages}쪽"
        elif deskewed_pages:
            status, detail = "확인필요", f"기울기 보정 스캔본: {deskewed_pages}쪽"
        elif cfg.get("review_on_nonzero") and any(totals):
            status, detail = "확인필요", "0이 아닌 출결값 확인"
        evidence = ", ".join(f"{year}학년 {pages_by_year[year]}쪽" for year in sorted(pages_by_year))
        return ExtractionResult(applicant_id, totals, status, detail, pdf_path.name, diagnostic_path, evidence)
    except Exception as exc:
        return ExtractionResult(applicant_id, [0] * 12, "확인필요", str(exc), pdf_path.name, diagnostic_path)
