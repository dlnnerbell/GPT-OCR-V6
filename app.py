from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import DEFAULT_CONFIG, load_config, save_config
from extractor import extract_pdf
from report import write_report


PROGRAM_VERSION = "v6"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.files: list[Path] = []
        self.output_path: Path | None = None
        self.title(f"{self.cfg['app_title']} {PROGRAM_VERSION}")
        self.geometry("760x500")
        self.minsize(700, 460)
        self._build()

    def _build(self):
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"생산직 공채 서류검증 {PROGRAM_VERSION}", font=("맑은 고딕", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="학교생활기록부 PDF의 출결상황을 지원자번호별로 Excel에 정리합니다.").pack(anchor="w", pady=(5, 22))
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        ttk.Button(bar, text="PDF 파일 선택", command=self.select_files).pack(side="left")
        ttk.Button(bar, text="설정", command=self.open_settings).pack(side="left", padx=8)
        self.count_label = ttk.Label(bar, text="선택된 파일: 0건")
        self.count_label.pack(side="left", padx=10)
        self.start_btn = ttk.Button(outer, text="서류 검증 시작", command=self.start, state="disabled")
        self.start_btn.pack(fill="x", pady=(24, 14), ipady=7)
        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.pack(fill="x")
        self.status = ttk.Label(outer, text="PDF 파일을 선택해 주세요.")
        self.status.pack(anchor="w", pady=9)
        self.log = tk.Text(outer, height=11, state="disabled", font=("맑은 고딕", 9))
        self.log.pack(fill="both", expand=True)
        self.open_btn = ttk.Button(outer, text="결과 폴더 열기", command=self.open_output, state="disabled")
        self.open_btn.pack(anchor="e", pady=(10, 0))

    def select_files(self):
        names = filedialog.askopenfilenames(title="학교생활기록부 PDF 선택", filetypes=[("PDF 파일", "*.pdf")])
        if not names:
            return
        self.files = [Path(x) for x in names]
        if len(self.files) > self.cfg["max_files"]:
            messagebox.showerror("파일 수 초과", f"최대 {self.cfg['max_files']}건까지 처리할 수 있습니다.")
            self.files = []
        self.count_label.config(text=f"선택된 파일: {len(self.files):,}건")
        self.start_btn.config(state="normal" if self.files else "disabled")

    def _append(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def start(self):
        output_dir = filedialog.askdirectory(title="결과 파일 저장 폴더 선택")
        if not output_dir:
            return
        self.start_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.progress["maximum"] = len(self.files)
        self.progress["value"] = 0
        threading.Thread(target=self._worker, args=(Path(output_dir),), daemon=True).start()

    def _worker(self, output_dir: Path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_path = output_dir / self.cfg["output_filename"].format(timestamp=timestamp)
        diagnostics = output_dir / f"확인용_출결표_{timestamp}"
        results = []
        for idx, path in enumerate(self.files, start=1):
            self.after(0, self.status.config, {"text": f"처리 중: {idx:,}/{len(self.files):,} - {path.name}"})
            result = extract_pdf(path, self.cfg, diagnostics)
            results.append(result)
            self.after(0, self.progress.config, {"value": idx})
            self.after(0, self._append, f"[{result.status}] {result.applicant_id or '(번호 없음)'}")
        write_report(results, self.cfg, self.output_path)
        review = sum(r.status != "정상" for r in results)
        self.after(0, self._done, len(results), review)

    def _done(self, total, review):
        self.status.config(text=f"완료: 총 {total:,}건 / 확인필요 {review:,}건 / {self.output_path}")
        self.start_btn.config(state="normal")
        self.open_btn.config(state="normal")
        messagebox.showinfo("서류 검증 완료", f"총 {total:,}건 처리가 완료되었습니다.\n확인필요: {review:,}건\n\n{self.output_path}")

    def open_output(self):
        if self.output_path:
            os.startfile(self.output_path.parent if os.name == "nt" else str(self.output_path.parent))

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("추출 기준 설정")
        win.geometry("620x570")
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="지원자번호 정규식").grid(row=0, column=0, sticky="w")
        pattern = tk.StringVar(value=self.cfg["applicant_id_pattern"])
        ttk.Entry(frame, textvariable=pattern, width=48).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="Excel 파일명").grid(row=1, column=0, sticky="w")
        filename = tk.StringVar(value=self.cfg["output_filename"])
        ttk.Entry(frame, textvariable=filename, width=48).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="전 항목 0 표시문구").grid(row=2, column=0, sticky="w")
        perfect = tk.StringVar(value=self.cfg.get("perfect_attendance_label", "3년개근"))
        ttk.Entry(frame, textvariable=perfect, width=48).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="출력 항목 (12개, 줄바꿈 구분)").grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 4))
        labels = tk.Text(frame, height=14)
        labels.grid(row=4, column=0, columnspan=2, sticky="nsew")
        labels.insert("1.0", "\n".join(self.cfg["attendance_labels"]))
        diagnostic = tk.BooleanVar(value=self.cfg["save_diagnostic_images"])
        ttk.Checkbutton(frame, text="검증용 출결표 이미지 저장", variable=diagnostic).grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        review = tk.BooleanVar(value=self.cfg["review_on_nonzero"])
        ttk.Checkbutton(frame, text="0이 아닌 출결값을 '확인필요'로 표시", variable=review).grid(row=6, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=14)
        def restore():
            pattern.set(DEFAULT_CONFIG["applicant_id_pattern"])
            filename.set(DEFAULT_CONFIG["output_filename"])
            perfect.set(DEFAULT_CONFIG["perfect_attendance_label"])
            labels.delete("1.0", "end")
            labels.insert("1.0", "\n".join(DEFAULT_CONFIG["attendance_labels"]))
            diagnostic.set(True); review.set(False)
        def commit():
            try:
                updated = dict(self.cfg)
                updated.update({"applicant_id_pattern": pattern.get(), "output_filename": filename.get(),
                                "perfect_attendance_label": perfect.get().strip() or "3년개근",
                                "attendance_labels": [x.strip() for x in labels.get("1.0", "end").splitlines() if x.strip()],
                                "save_diagnostic_images": diagnostic.get(), "review_on_nonzero": review.get()})
                save_config(updated)
                self.cfg = load_config()
                win.destroy()
                messagebox.showinfo("저장 완료", "설정이 config.json에 저장되었습니다.")
            except Exception as exc:
                messagebox.showerror("설정 오류", str(exc))
        ttk.Button(buttons, text="기본값 복원", command=restore).pack(side="left", padx=6)
        ttk.Button(buttons, text="저장", command=commit).pack(side="left")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)


if __name__ == "__main__":
    App().mainloop()
