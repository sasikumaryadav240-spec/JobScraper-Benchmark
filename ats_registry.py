"""
Verified Employer ATS Board Registry.

Maintains verified, publicly accessible employer job board identifiers
across Greenhouse, Lever, and Ashby platforms.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class EmployerBoard:
    company_name: str
    ats_platform: str  # 'greenhouse' | 'lever' | 'ashby'
    board_identifier: str  # public slug/token
    board_url: str
    country_or_market: str
    verification_timestamp: str
    discovery_source: str
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Configurable verified employer boards
VERIFIED_EMPLOYER_BOARDS: list[EmployerBoard] = [
    # --- Greenhouse Boards ---
    EmployerBoard(
        company_name="Netskope",
        ats_platform="greenhouse",
        board_identifier="netskope",
        board_url="https://boards-api.greenhouse.io/v1/boards/netskope/jobs?content=true",
        country_or_market="India & Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_job_board",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Rubrik",
        ats_platform="greenhouse",
        board_identifier="rubrik",
        board_url="https://boards-api.greenhouse.io/v1/boards/rubrik/jobs?content=true",
        country_or_market="India & Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_job_board",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Elastic",
        ats_platform="greenhouse",
        board_identifier="elastic",
        board_url="https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true",
        country_or_market="India & Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_job_board",
        is_active=True,
    ),
    EmployerBoard(
        company_name="GitLab",
        ats_platform="greenhouse",
        board_identifier="gitlab",
        board_url="https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true",
        country_or_market="India & Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_job_board",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Cloudflare",
        ats_platform="greenhouse",
        board_identifier="cloudflare",
        board_url="https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs?content=true",
        country_or_market="Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_job_board",
        is_active=True,
    ),
    EmployerBoard(
        company_name="MongoDB",
        ats_platform="greenhouse",
        board_identifier="mongodb",
        board_url="https://boards-api.greenhouse.io/v1/boards/mongodb/jobs?content=true",
        country_or_market="India & Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_job_board",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Okta",
        ats_platform="greenhouse",
        board_identifier="okta",
        board_url="https://boards-api.greenhouse.io/v1/boards/okta/jobs?content=true",
        country_or_market="India & Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_job_board",
        is_active=True,
    ),

    # --- Lever Boards ---
    EmployerBoard(
        company_name="Meesho",
        ats_platform="lever",
        board_identifier="meesho",
        board_url="https://api.lever.co/v0/postings/meesho?mode=json",
        country_or_market="India",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_postings_api",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Palantir",
        ats_platform="lever",
        board_identifier="palantir",
        board_url="https://api.lever.co/v0/postings/palantir?mode=json",
        country_or_market="Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_postings_api",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Spotify",
        ats_platform="lever",
        board_identifier="spotify",
        board_url="https://api.lever.co/v0/postings/spotify?mode=json",
        country_or_market="Global",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_postings_api",
        is_active=True,
    ),

    # --- Ashby Boards ---
    EmployerBoard(
        company_name="Vanta",
        ats_platform="ashby",
        board_identifier="vanta",
        board_url="https://api.ashbyhq.com/posting-api/job-board/vanta",
        country_or_market="Global / Remote",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_posting_api",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Sentry",
        ats_platform="ashby",
        board_identifier="sentry",
        board_url="https://api.ashbyhq.com/posting-api/job-board/sentry",
        country_or_market="Global / Remote",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_posting_api",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Ramp",
        ats_platform="ashby",
        board_identifier="ramp",
        board_url="https://api.ashbyhq.com/posting-api/job-board/ramp",
        country_or_market="Global / Remote",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_posting_api",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Airbyte",
        ats_platform="ashby",
        board_identifier="airbyte",
        board_url="https://api.ashbyhq.com/posting-api/job-board/airbyte",
        country_or_market="Global / Remote",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_posting_api",
        is_active=True,
    ),
    EmployerBoard(
        company_name="Supabase",
        ats_platform="ashby",
        board_identifier="supabase",
        board_url="https://api.ashbyhq.com/posting-api/job-board/supabase",
        country_or_market="Global Remote",
        verification_timestamp="2026-11-19T03:39:00Z",
        discovery_source="verified_public_posting_api",
        is_active=True,
    ),
]


def get_verified_registry(
    platform: str | None = None,
    active_only: bool = True,
) -> list[EmployerBoard]:
    """
    Retrieve configured employer boards, optionally filtered by platform and active status.
    """
    boards = VERIFIED_EMPLOYER_BOARDS
    if active_only:
        boards = [b for b in boards if b.is_active]
    if platform:
        boards = [b for b in boards if b.ats_platform.lower() == platform.lower()]
    return boards
