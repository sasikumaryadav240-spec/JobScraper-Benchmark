"""
Authoritative Candidate Skills Profile.

Defines the exact education, skills, evidence tiers, and restrictions
for candidate matching in Phase 5.
"""

from __future__ import annotations

from typing import Any

CANDIDATE_PROFILE: dict[str, Any] = {
    "candidate": {
        "degree": "B.E. Electronics and Communication Engineering",
        "institution": "Saveetha School of Engineering",
        "graduation_year": 2026,
        "target": "Fresher cybersecurity / adjacent IT roles",
    },
    "experience": [
        {
            "role": "MERN Stack Developer Intern",
            "company": "Varnarc Pvt. Ltd.",
            "start": "2026-01",
            "end": "2026-04",
            "type": "software_development",
            "is_security_experience": False,
            "note": "Software development only. Never represent as SOC / cybersecurity / information-security experience.",
        }
    ],
    "evidence_levels": {
        "professional": {"description": "Used in paid professional employment", "max_match": 1.0},
        "practical": {"description": "Hands-on lab / project practice, not employment", "max_match": 0.75},
        "basic": {"description": "Foundational or transferable knowledge", "max_match": 0.4},
        "none": {"description": "No evidence", "max_match": 0.0},
    },
    "eligibility": {
        "degree": "B.E. Electronics and Communication Engineering",
        "branch": "Electronics and Communication Engineering",
        "branch_aliases": [
            "ece",
            "electronics and communication",
            "electronics & communication",
            "electronics and telecommunication",
            "electronics",
        ],
        "is_cs_it_degree": False,
        "graduation_year": 2026,
        "professional_cybersecurity_experience_years": 0.0,
    },
    "skills": [
        # SIEM
        {"name": "Splunk", "category": "SIEM", "evidence_level": "practical"},
        {"name": "Microsoft Sentinel", "category": "SIEM", "evidence_level": "practical", "aliases": ["Azure Sentinel", "MS Sentinel"]},
        {"name": "KQL", "category": "SIEM", "evidence_level": "practical", "aliases": ["Kusto Query Language", "Kusto"]},
        {"name": "Log Analysis", "category": "SIEM", "evidence_level": "practical", "aliases": ["log analytics", "log review", "log monitoring"]},
        {"name": "SIEM", "category": "SIEM", "evidence_level": "practical", "aliases": ["SIEM tools", "security information and event management"]},

        # SOC Operations
        {"name": "Alert Investigation", "category": "SOC Operations", "evidence_level": "practical", "aliases": ["alert analysis", "alert triage", "investigate alerts", "investigating alerts"]},
        {"name": "Incident Triage", "category": "SOC Operations", "evidence_level": "practical", "aliases": ["triage", "incident analysis"]},
        {"name": "Security Monitoring", "category": "SOC Operations", "evidence_level": "practical", "aliases": ["security event monitoring", "24x7 monitoring", "24/7 monitoring", "SOC monitoring", "threat monitoring"]},
        {"name": "Incident Response Fundamentals", "category": "SOC Operations", "evidence_level": "basic", "aliases": ["incident response", "incident handling", "IR"]},

        # EDR
        {"name": "LimaCharlie EDR", "category": "EDR", "evidence_level": "practical", "aliases": ["LimaCharlie"]},
        {"name": "Endpoint Monitoring", "category": "EDR", "evidence_level": "practical", "aliases": ["EDR", "endpoint detection", "endpoint security", "endpoint protection"]},

        # Networking
        {"name": "TCP/IP", "category": "Networking", "evidence_level": "basic", "aliases": ["TCP IP", "networking fundamentals", "network protocols", "OSI model"]},
        {"name": "DNS", "category": "Networking", "evidence_level": "basic"},
        {"name": "DHCP", "category": "Networking", "evidence_level": "basic"},
        {"name": "VPN", "category": "Networking", "evidence_level": "basic"},
        {"name": "Firewall Concepts", "category": "Networking", "evidence_level": "basic", "aliases": ["firewall", "firewalls", "IDS", "IPS", "IDS/IPS"]},
        {"name": "Wireshark", "category": "Networking", "evidence_level": "practical", "aliases": ["packet analysis", "packet capture", "pcap"]},

        # Operating Systems
        {"name": "Linux", "category": "Operating Systems", "evidence_level": "practical", "aliases": ["unix"]},
        {"name": "Windows", "category": "Operating Systems", "evidence_level": "practical", "aliases": ["windows server", "windows os"]},

        # Scripting
        {"name": "Bash", "category": "Scripting", "evidence_level": "basic", "aliases": ["shell scripting", "shell script"]},
        {"name": "PowerShell", "category": "Scripting", "evidence_level": "basic"},
        {"name": "Python Basics", "category": "Scripting", "evidence_level": "basic", "aliases": ["Python", "python scripting"]},

        # Vulnerability Management
        {"name": "Nessus", "category": "Vulnerability Management", "evidence_level": "practical", "aliases": ["Tenable"]},
        {"name": "Vulnerability Assessment", "category": "Vulnerability Management", "evidence_level": "practical", "aliases": ["vulnerability management", "vulnerability scanning", "VA", "vulnerability analysis"]},
        {"name": "OWASP Concepts", "category": "Vulnerability Management", "evidence_level": "basic", "aliases": ["OWASP", "OWASP Top 10", "web application security"]},

        # Cloud Security
        {"name": "Azure Exposure", "category": "Cloud Security", "evidence_level": "basic", "aliases": ["Azure", "Microsoft Azure"]},
        {"name": "Basic Cloud Security", "category": "Cloud Security", "evidence_level": "basic", "aliases": ["cloud security", "cloud fundamentals"]},
        {"name": "Terraform Basics", "category": "Cloud Security", "evidence_level": "basic", "aliases": ["Terraform", "infrastructure as code", "IaC"]},

        # Containers
        {"name": "Docker Basics", "category": "Containers", "evidence_level": "basic", "aliases": ["Docker", "containers", "containerization"]},
        {"name": "Kubernetes Basics", "category": "Containers", "evidence_level": "basic", "aliases": ["Kubernetes", "K8s"]},

        # Security Fundamentals
        {"name": "Threat Analysis", "category": "Security Fundamentals", "evidence_level": "basic", "aliases": ["threat intelligence", "threat detection", "threat modeling", "MITRE ATT&CK", "MITRE"]},
        {"name": "Access Control", "category": "Security Fundamentals", "evidence_level": "basic", "aliases": ["IAM", "identity and access management", "least privilege", "RBAC"]},
        {"name": "Information Security", "category": "Security Fundamentals", "evidence_level": "basic", "aliases": ["infosec", "cyber security", "cybersecurity", "security fundamentals", "CIA triad"]},
        {"name": "Risk Management", "category": "Security Fundamentals", "evidence_level": "basic", "aliases": ["risk assessment"]},

        # Development
        {"name": "Java", "category": "Development", "evidence_level": "practical"},
        {"name": "JavaScript", "category": "Development", "evidence_level": "professional", "aliases": ["JS"]},
        {"name": "TypeScript", "category": "Development", "evidence_level": "professional"},
        {"name": "Node.js", "category": "Development", "evidence_level": "professional", "aliases": ["NodeJS", "Node"]},
        {"name": "Express.js", "category": "Development", "evidence_level": "professional", "aliases": ["Express", "ExpressJS"]},
        {"name": "React", "category": "Development", "evidence_level": "professional", "aliases": ["React.js", "ReactJS"]},
        {"name": "MongoDB", "category": "Development", "evidence_level": "professional", "aliases": ["Mongo"]},
        {"name": "PostgreSQL", "category": "Development", "evidence_level": "practical", "aliases": ["Postgres"]},
        {"name": "MERN Stack", "category": "Development", "evidence_level": "professional", "aliases": ["MERN"]},
    ],
    "restrictions": [
        {"id": "production_soc", "description": "No production SOC experience; labs are not employment.", "patterns": ["production soc", "soc experience", "years of soc", "live soc", "soc analyst experience"], "max_match": 0.4},
        {"id": "defender_xdr", "description": "No advanced Microsoft Defender XDR experience.", "patterns": ["defender xdr", "microsoft defender for endpoint", "defender for endpoint", "defender for cloud apps", "defender for identity", "microsoft 365 defender"], "max_match": 0.4},
        {"id": "soar", "description": "No advanced SOAR experience.", "patterns": ["soar", "playbook automation", "cortex xsoar", "xsoar", "phantom", "siemplify", "logic apps"], "max_match": 0.4},
        {"id": "aws_security", "description": "No advanced AWS security engineering experience.", "patterns": ["aws security", "guardduty", "security hub", "aws iam", "aws waf", "aws config", "cloudtrail"], "max_match": 0.4},
        {"id": "threat_hunting", "description": "No professional threat hunting experience.", "patterns": ["threat hunting", "threat hunt", "proactive hunting"], "max_match": 0.4},
        {"id": "ir_ownership", "description": "No professional incident response ownership.", "patterns": ["incident commander", "lead incident response", "own incident", "incident response lead", "ir lead", "dfir"], "max_match": 0.4},
        {"id": "grc_audit", "description": "No professional GRC audit / control-testing experience.", "patterns": ["control testing", "audit", "sox", "internal audit", "compliance audit", "evidence collection", "grc assessment"], "max_match": 0.4},
    ],
}

