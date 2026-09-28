"""Profile and attempt models used by the academic engine (independent of HTTP/DB)."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CODE_RX = re.compile(r"^([A-Z]{2,6})\s*([FGUCEKNZ])\s?(\d{3})([A-Z]?)$")


def normalize_code(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip().upper())
    m = CODE_RX.match(s)
    if not m:
        raise ValueError(f"'{raw}' is not a course number like 'CS F211'")
    return f"{m.group(1)} {m.group(2)}{m.group(3)}{m.group(4)}"


class CourseAttempt(BaseModel):
    course_code: str
    status: Literal["completed", "in_progress"] = "completed"
    grade: str | None = Field(None, description="Letter grade (A..E), non-letter grade (GOOD, CLR...) or report (NC, W, RC, I, GA)")
    term: str | None = None
    attempt_order: int | None = Field(None, description="1 for first attempt, 2 for a repeat, ...")
    credit_value: int | None = None
    credit_system: Literal["units", "credit_hours"] | None = None

    @field_validator("course_code")
    @classmethod
    def _code(cls, v: str) -> str:
        return normalize_code(v)

    @field_validator("grade")
    @classmethod
    def _grade(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else None


class Approval(BaseModel):
    type: Literal["prerequisite_waiver", "dca_prior_preparation", "hd_course_permission", "minor_admission",
                  "extra_elective_permission", "other"]
    course_code: str | None = None
    note: str | None = None

    @field_validator("course_code")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        return normalize_code(v) if v else None


class StudentProfile(BaseModel):
    campus: str = "Pilani"
    admission_year: int = Field(..., ge=2000, le=2100)
    programmes: list[str] = Field(..., min_length=1, max_length=2, description="Programme ids, e.g. ['BE-COMPUTER-SCIENCE']")
    current_semester: int = Field(..., ge=1, le=12, description="1 = year I first semester, 3 = year II first semester ...")
    current_term: str = "2026-27-S1"
    minor: str | None = None
    interests: list[str] = Field(default_factory=list)
    attempts: list[CourseAttempt] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    cgpa: float | None = Field(None, ge=0, le=10)
    curriculum_attested: bool = Field(False, description="Student confirms the bulletin 2025-26 chart applies (non-2025 cohorts)")
    display_name: str | None = None

    @property
    def year_sem(self) -> tuple[str, int]:
        years = ["I", "II", "III", "IV", "V", "VI"]
        n = self.current_semester
        return years[(n - 1) // 2], 1 if n % 2 == 1 else 2


YEAR_ORDER = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}


def sem_index(year: str, sem: int) -> int:
    return (YEAR_ORDER.get(year, 9) - 1) * 2 + sem
