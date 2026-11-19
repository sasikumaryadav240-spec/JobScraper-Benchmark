"""
Unified Pipeline Orchestrator and Exporter for Phase 5.

Coordinates multi-source ingestion, cross-platform deduplication,
candidate matching, categorization, and multi-format exports (JSON, CSV, Excel).
"""

from __future__ import annotations

import csv
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

# Windows registry permission error guard for openpyxl
try:
    mimetypes.init()
except PermissionError:
    mimetypes._winreg = None
    mimetypes.init = lambda *args, **kwargs: None

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from candidate_matching_engine import evaluate_candidate_matching
from unified_dedup import deduplicate_cross_source
from unified_ingestion import ingest_all_sources
from unified_models import CanonicalJobRecord, EligibilityStatus, JobCategory

logger = logging.getLogger("UnifiedPipeline")


class UnifiedPipeline:
    def __init__(self, base_dir: str | Path = "jobspy"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict[str, Any]:
        """Execute full Phase 5 unified pipeline."""
        logger.info("Step 1: Ingesting all completed source datasets...")
        raw_canonical, ingestion_summary = ingest_all_sources(self.base_dir)

        logger.info("Step 2: Performing cross-source deduplication...")
        unique_records, dedup_audit = deduplicate_cross_source(raw_canonical)

        logger.info("Step 3: Evaluating candidate matching, scoring, and eligibility...")
        processed_records: list[CanonicalJobRecord] = []
        for rec in unique_records:
            evaluated = evaluate_candidate_matching(rec)
            processed_records.append(evaluated)

        logger.info("Step 4: Categorizing records...")
        matched_jobs = [r for r in processed_records if r.eligibility_status == EligibilityStatus.PASS_STATUS.value and r.overall_match_score > 60.0]
        # Sort matched jobs descending by overall match score
        matched_jobs.sort(key=lambda x: x.overall_match_score, reverse=True)

        supporting_it_jobs = [r for r in processed_records if r.job_category == JobCategory.SUPPORTING_IT.value]
        internship_jobs = [r for r in processed_records if r.job_category == JobCategory.INTERNSHIP.value]
        manual_review_jobs = [r for r in processed_records if r.eligibility_status == EligibilityStatus.REVIEW_STATUS.value]
        excluded_jobs = [r for r in processed_records if r.eligibility_status == EligibilityStatus.EXCLUDE_STATUS.value]

        logger.info("Step 5: Exporting JSON, CSV, and Excel datasets...")
        self._export_json(self.base_dir / "unified_jobs.json", processed_records)
        self._export_csv(self.base_dir / "unified_jobs.csv", processed_records)
        self._export_excel(self.base_dir / "unified_jobs.xlsx", processed_records, "All Unified Jobs")

        self._export_json(self.base_dir / "matched_jobs.json", matched_jobs)
        self._export_csv(self.base_dir / "matched_jobs.csv", matched_jobs)
        self._export_excel(self.base_dir / "matched_jobs.xlsx", matched_jobs, "Matched Jobs (>60% Score)")

        self._export_json(self.base_dir / "supporting_it_jobs.json", supporting_it_jobs)
        self._export_json(self.base_dir / "internships.json", internship_jobs)
        self._export_json(self.base_dir / "manual_verification_jobs.json", manual_review_jobs)
        self._export_json(self.base_dir / "excluded_jobs.json", excluded_jobs)

        # Export duplicate audit
        with open(self.base_dir / "duplicate_audit.json", "w", encoding="utf-8") as f:
            json.dump(dedup_audit.to_dict(), f, indent=2, default=str)

        # Generate Matching Report
        matching_report = self._build_matching_report(
            processed_records, matched_jobs, supporting_it_jobs, internship_jobs, manual_review_jobs, excluded_jobs
        )
        with open(self.base_dir / "matching_report.json", "w", encoding="utf-8") as f:
            json.dump(matching_report, f, indent=2, default=str)

        # Generate Unified Quality Report
        quality_report = self._build_quality_report(
            ingestion_summary, dedup_audit, processed_records
        )
        with open(self.base_dir / "unified_quality_report.json", "w", encoding="utf-8") as f:
            json.dump(quality_report, f, indent=2, default=str)

        summary_result = {
            "total_ingested_raw": ingestion_summary["total_raw_ingested"],
            "total_canonical_unique": len(processed_records),
            "duplicates_merged": dedup_audit.duplicates_merged_total,
            "matched_jobs_count": len(matched_jobs),
            "supporting_it_count": len(supporting_it_jobs),
            "internships_count": len(internship_jobs),
            "manual_review_count": len(manual_review_jobs),
            "excluded_count": len(excluded_jobs),
            "output_directory": str(self.base_dir.resolve()),
        }
        logger.info(f"Phase 5 Pipeline Complete: {summary_result}")
        return summary_result

    def _export_json(self, path: Path, records: list[CanonicalJobRecord]) -> None:
        data = [r.model_dump(mode="json") for r in records]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def _export_csv(self, path: Path, records: list[CanonicalJobRecord]) -> None:
        if not records:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                f.write("")
            return

        flat_rows = [r.to_flat_dict() for r in records]
        keys = list(flat_rows[0].keys())
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(flat_rows)

    def _export_excel(self, path: Path, records: list[CanonicalJobRecord], sheet_name: str) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_name[:31]

        if not records:
            ws.append(["No records found matching criteria"])
            wb.save(path)
            return

        flat_rows = [r.to_flat_dict() for r in records]
        headers = list(flat_rows[0].keys())

        # Header style
        header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in enumerate(flat_rows, start=2):
            row_vals = [row.get(h) for h in headers]
            ws.append(row_vals)

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col[:50])
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

        wb.save(path)

    def _build_matching_report(
        self,
        all_records: list[CanonicalJobRecord],
        matched: list[CanonicalJobRecord],
        supporting: list[CanonicalJobRecord],
        internships: list[CanonicalJobRecord],
        manual: list[CanonicalJobRecord],
        excluded: list[CanonicalJobRecord],
    ) -> dict[str, Any]:
        score_bins = {"0-20%": 0, "20-40%": 0, "40-60%": 0, "60-80%": 0, "80-100%": 0}
        for r in all_records:
            s = r.overall_match_score
            if s < 20.0:
                score_bins["0-20%"] += 1
            elif s < 40.0:
                score_bins["20-40%"] += 1
            elif s <= 60.0:
                score_bins["40-60%"] += 1
            elif s <= 80.0:
                score_bins["60-80%"] += 1
            else:
                score_bins["80-100%"] += 1

        rejection_counts: dict[str, int] = {}
        for r in excluded:
            for reason in r.rejection_reasons:
                prefix = reason.split(":")[0] if ":" in reason else reason[:40]
                rejection_counts[prefix] = rejection_counts.get(prefix, 0) + 1

        return {
            "total_evaluated_records": len(all_records),
            "matched_jobs_count": len(matched),
            "supporting_it_count": len(supporting),
            "internships_count": len(internships),
            "manual_review_count": len(manual),
            "excluded_jobs_count": len(excluded),
            "score_distribution": score_bins,
            "top_rejection_reasons": rejection_counts,
            "candidate_profile_applied": {
                "degree": "B.E. Electronics and Communication Engineering",
                "institution": "Saveetha School of Engineering",
                "graduation_year": 2026,
                "professional_security_years": 0.0,
                "overall_threshold": "STRICTLY > 60.0%",
            },
        }

    def _build_quality_report(
        self,
        ingestion_summary: dict[str, Any],
        dedup_audit: Any,
        records: list[CanonicalJobRecord],
    ) -> dict[str, Any]:
        total = len(records)
        def coverage(predicate) -> dict[str, Any]:
            c = sum(1 for r in records if predicate(r))
            return {"count": c, "pct": round((c / total * 100.0) if total else 0.0, 2)}

        # Source breakdown
        by_src: dict[str, int] = {}
        for r in records:
            by_src[r.source] = by_src.get(r.source, 0) + 1

        return {
            "ingestion": ingestion_summary,
            "deduplication": dedup_audit.to_dict(),
            "canonical_dataset_quality": {
                "total_unique_records": total,
                "source_breakdown": by_src,
                "field_coverage": {
                    "title": coverage(lambda r: bool(r.job_title)),
                    "company": coverage(lambda r: bool(r.company_name)),
                    "location": coverage(lambda r: bool(r.normalized_location)),
                    "date_posted": coverage(lambda r: bool(r.normalized_posted_date)),
                    "job_url": coverage(lambda r: bool(r.original_job_url)),
                    "application_url": coverage(lambda r: bool(r.application_url)),
                    "full_description": coverage(lambda r: r.description_type == "full_description"),
                    "search_result_snippet": coverage(lambda r: r.description_type == "search_result_snippet"),
                    "min_experience": coverage(lambda r: r.mandatory_min_experience is not None),
                    "work_mode_known": coverage(lambda r: r.work_mode != "unknown"),
                    "remote_eligibility": coverage(lambda r: bool(r.remote_eligibility)),
                },
                "work_mode_distribution": {
                    "remote": sum(1 for r in records if r.work_mode == "remote"),
                    "hybrid": sum(1 for r in records if r.work_mode == "hybrid"),
                    "onsite": sum(1 for r in records if r.work_mode == "onsite"),
                    "unknown": sum(1 for r in records if r.work_mode == "unknown"),
                },
            },
        }

