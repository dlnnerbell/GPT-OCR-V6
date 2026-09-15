from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path


DEFAULT_CONFIG = {
    "app_title": "생산직 공채 서류검증",
    "applicant_id_pattern": r"\d{6}-\d{6}",
    "unexcused_label": "미인정",
    "perfect_attendance_label": "3년개근",
    "output_filename": "서류검증결과_{timestamp}.xlsx",
    "attendance_labels": [
        "질병결석", "미인정결석", "기타결석",
        "질병지각", "미인정지각", "기타지각",
        "질병조퇴", "미인정조퇴", "기타조퇴",
        "질병결과", "미인정결과", "기타결과",
    ],
    "max_files": 1000,
    "render_dpi": 240,
    "save_diagnostic_images": True,
    "review_on_nonzero": False,
}


def config_path() -> Path:
    return Path.cwd() / "config.json"


def load_config() -> dict:
    path = config_path()
    cfg = deepcopy(DEFAULT_CONFIG)
    if path.exists():
        try:
            incoming = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(incoming, dict):
                cfg.update(incoming)
        except (OSError, json.JSONDecodeError):
            pass
    return validate_config(cfg)


def validate_config(cfg: dict) -> dict:
    re.compile(str(cfg["applicant_id_pattern"]))
    labels = cfg.get("attendance_labels")
    if not isinstance(labels, list) or len(labels) != 12 or not all(str(x).strip() for x in labels):
        raise ValueError("출결 출력 항목은 정확히 12개여야 합니다.")
    cfg["attendance_labels"] = [str(x).strip() for x in labels]
    cfg["max_files"] = max(1, min(5000, int(cfg.get("max_files", 1000))))
    cfg["render_dpi"] = max(150, min(400, int(cfg.get("render_dpi", 240))))
    cfg["save_diagnostic_images"] = bool(cfg.get("save_diagnostic_images", True))
    cfg["review_on_nonzero"] = bool(cfg.get("review_on_nonzero", False))
    return cfg


def save_config(cfg: dict) -> None:
    cfg = validate_config(cfg)
    config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
