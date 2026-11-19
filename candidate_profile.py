"""
Authoritative Candidate Skills Profile - 15 Role Families
Version: 2.0 | Updated: 9 November 2026
Source: Candidate_Skills(4).txt

Defines:
- 15 Role Families (10 Cybersecurity + 5 IT Support & Infrastructure)
- Strict proficiency distinctions:
    1. Professional experience (paid employment/internship)
    2. Hands-on / practical (labs, projects, exercises - NOT employment)
    3. Familiarity / fundamentals (conceptual, limited exposure)
    4. Learning target --- not yet verified (added for planning - NEVER credited as existing skill)
    5. Certification (credential - NOT employment)
- Alternative-tool rules
- Part E Section 4 Weighted Scoring Model (35% / 25% / 15% / 10% / 5% / 10%)
- Strict threshold override (> 60.0%)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Authoritative weights from Part E, Section 4
SCORING_WEIGHTS = {
    "required_technical_skills": 0.35,
    "core_responsibilities": 0.25,
    "experience_compatibility": 0.15,
    "tools_and_technologies": 0.10,
    "education_and_certifications": 0.05,
    "transferable_skills": 0.10,
}

# Verify weights sum exactly to 1.0 (100%)
assert round(sum(SCORING_WEIGHTS.values()), 4) == 1.0, "Weights must sum to 100%"

CANDIDATE_PROFILE: dict[str, Any] = {
    "version": "2.0",
    "updated_date": "2026-11-09",
    "source_file": "Candidate_Skills(4).txt",
    "candidate": {
        "degree": "B.E. Electronics and Communication Engineering",
        "institution": "Saveetha School of Engineering, Chennai",
        "batch": "2022-2026",
        "graduation_year": 2026,
        "cgpa": "7.94/10",
        "target": "Fresher cybersecurity / adjacent IT roles",
    },
    "certifications": [
        {"name": "CompTIA Security+", "status": "Passed", "is_employment": False},
        {"name": "Google Cybersecurity Professional Certificate", "status": "Completed", "is_employment": False},
    ],
    "experience": [
        {
            "role": "MERN Stack Developer Intern",
            "company": "Varnarc Pvt. Ltd.",
            "period": "January 2026 -- April 2026",
            "type": "software_development",
            "is_cybersecurity_employment": False,
            "is_it_support_employment": False,
            "is_system_admin_employment": False,
            "is_network_admin_employment": False,
            "recorded_skills": [
                "Node.js", "Express.js", "MongoDB", "REST APIs",
                "JWT authentication", "RBAC", "least privilege",
                "Input validation", "structured logging", "exception handling",
                "Troubleshooting", "application log investigation", "security issue investigation",
                "Root cause analysis", "documentation", "secure development practices",
            ],
            "note": (
                "This is professional software-development experience, not professional SOC, "
                "cybersecurity, IT support, system administration or network administration employment. "
                "Never count toward mandatory cybersecurity or IT infrastructure employment requirements."
            ),
        }
    ],
    "proficiency_levels": {
        "professional": {"description": "Used during paid employment/internship", "weight": 1.0, "is_employment": True},
        "practical": {"description": "Hands-on lab/project practice, not employment", "weight": 1.0, "is_employment": False},
        "familiarity": {"description": "Conceptual or limited exposure, not independent admin", "weight": 0.4, "is_employment": False},
        "learning_target": {"description": "Unverified target from curriculum - 0 credit", "weight": 0.0, "is_employment": False},
        "certification": {"description": "Credential, not employment", "weight": 1.0, "is_employment": False},
    },
    "alternative_tools": {
        "siem": {
            "candidate_platforms": ["Microsoft Sentinel", "Splunk"],
            "alternatives": ["Splunk", "Microsoft Sentinel", "Sentinel", "Elastic Security", "QRadar", "LogRhythm", "ArcSight"],
            "rule": "Splunk OR Sentinel: credit the platform actually used; do not require both unless explicitly required.",
        },
        "vulnerability_management": {
            "candidate_platforms": ["Nessus"],
            "alternatives": ["Nessus", "Qualys", "OpenVAS", "Rapid7 InsightVM", "Greenbone"],
            "rule": "Nessus / Qualys / OpenVAS: credit practical Nessus use and note missing platform.",
        },
        "edr": {
            "candidate_platforms": ["Microsoft Defender XDR", "LimaCharlie EDR"],
            "alternatives": ["Microsoft Defender XDR", "LimaCharlie", "CrowdStrike", "SentinelOne"],
            "rule": "EDR alternatives: credit demonstrated Defender XDR / LimaCharlie exposure while noting missing vendor-specific production experience.",
        },
        "itsm": {
            "candidate_platforms": ["ITSM Concepts"],
            "alternatives": ["ServiceNow", "Jira Service Management", "Freshservice"],
            "rule": "ServiceNow / Jira Service Management / Freshservice: ITSM concepts do not equal hands-on platform administration.",
        },
        "virtualization": {
            "candidate_platforms": ["Virtualization fundamentals"],
            "alternatives": ["Hyper-V", "VMware", "ESXi", "vSphere", "VirtualBox", "Proxmox"],
            "rule": "Hyper-V / VMware: virtualization fundamentals do not equal production hypervisor administration.",
        },
        "cloud": {
            "candidate_platforms": ["Azure Exposure", "AWS Fundamentals"],
            "alternatives": ["Microsoft Azure", "AWS", "Google Cloud"],
            "rule": "Azure / AWS: cloud fundamentals do not equal production cloud engineering.",
        },
        "networking": {
            "candidate_platforms": ["Wireshark", "TCP/IP", "Networking Fundamentals"],
            "alternatives": ["Cisco", "Juniper", "Aruba", "Palo Alto", "Fortinet", "Check Point", "F5", "Zscaler", "Netskope"],
            "rule": "Cisco / Juniper / Aruba: general networking knowledge transfers, but vendor configuration experience must not be invented.",
        },
    },
    "role_families": [
        # --- PART A: CYBERSECURITY ROLE FAMILIES ---
        {
            "id": 1,
            "name": "SOC Analyst / Security Operations",
            "group": "cybersecurity",
            "title_patterns": [r"\bsoc\b", r"security operations", r"security operations center", r"soc analyst", r"l1 soc", r"tier 1 soc"],
            "practical_skills": [
                "Microsoft Sentinel", "KQL", "Splunk", "SPL", "Elastic Security",
                "Microsoft Defender XDR", "LimaCharlie EDR", "Sysmon", "Windows Event Logs",
                "Wireshark", "PowerShell", "Bash", "Security Monitoring", "Alert Triage",
                "Alert Investigation", "Incident Investigation", "Event Correlation",
                "False-Positive Analysis", "MITRE ATT&CK", "Incident Response Lifecycle",
            ],
            "familiarity_skills": [
                "IBM QRadar", "RSA NetWitness", "LogRhythm", "ArcSight", "CrowdStrike concepts", "SentinelOne",
            ],
            "learning_targets": [
                "Sigma CLI", "Shuffle SOAR", "Advanced Defender XDR", "ATT&CK Navigator",
                "MISP", "Zeek", "YARA", "CyberChef", "VirusTotal enrichment", "TheHive", "Logic Apps automation",
            ],
        },
        {
            "id": 2,
            "name": "Security Analyst / Information Security Analyst",
            "group": "cybersecurity",
            "title_patterns": [r"security analyst", r"information security analyst", r"infosec analyst", r"cyber security analyst", r"cybersecurity analyst"],
            "practical_skills": [
                "Nessus", "Sentinel", "Splunk", "Windows Event Logs", "Wireshark", "PowerShell",
                "Security Monitoring", "Vulnerability Assessment", "Severity Analysis",
                "Risk-Based Prioritization", "Remediation Recommendations", "Security Hardening",
            ],
            "familiarity_skills": [
                "Entra ID fundamentals", "NIST CSF fundamentals", "CIS Controls fundamentals",
                "ISO 27001 fundamentals", "Third-party risk fundamentals", "Microsoft Defender concepts",
            ],
            "learning_targets": [
                "Defender for Endpoint operational administration", "Microsoft Purview",
                "ServiceNow GRC/IRM", "AuditBoard", "Vanta", "Drata", "OneTrust",
                "CIS-CAT", "OpenSCAP", "Power BI security reporting", "Control testing workflows",
            ],
        },
        {
            "id": 3,
            "name": "SIEM / Detection / Incident Response",
            "group": "cybersecurity",
            "title_patterns": [r"\bsiem\b", r"detection engineer", r"incident response", r"\bir analyst\b", r"detection and response", r"threat detection"],
            "practical_skills": [
                "Sentinel", "KQL", "Splunk", "SPL", "Elastic Security", "Windows Event Logs",
                "Sysmon", "LimaCharlie EDR", "SIEM Monitoring", "Log Analysis", "Detection Rule Creation",
                "Detection Tuning", "Dashboards", "Threat Hunting", "Suspicious PowerShell Detection",
                "Brute-Force Detection", "Account Lockout Analysis",
            ],
            "familiarity_skills": [
                "QRadar", "RSA NetWitness", "LogRhythm", "ArcSight",
            ],
            "learning_targets": [
                "Sigma", "Sigma CLI", "Shuffle SOAR", "TheHive", "MISP", "Zeek", "YARA",
                "CyberChef", "ATT&CK Navigator", "Detection-as-code", "Version-controlled rule testing",
            ],
        },
        {
            "id": 4,
            "name": "Security Engineering",
            "group": "cybersecurity",
            "title_patterns": [r"security engineer", r"infosec engineer", r"cyber security engineer", r"cybersecurity engineer"],
            "practical_skills": [
                "PowerShell", "Bash", "Linux", "Windows", "Docker", "Kubernetes fundamentals",
                "Terraform exposure", "Nessus", "Wireshark", "Sysmon", "LimaCharlie",
                "Firewall concepts", "API Security", "IAM", "RBAC", "Least Privilege",
            ],
            "familiarity_skills": [
                "Defender security concepts", "Network security", "Hardening fundamentals",
            ],
            "learning_targets": [
                "Trivy", "Checkov", "TFLint", "OPA", "Conftest", "Ansible", "OpenSCAP",
                "CIS-CAT", "OpenSSL", "Azure Key Vault", "HashiCorp Vault", "Falco",
                "Kubescape", "Syft", "Secure CI/CD", "IaC security testing", "Threat Modeling Tool",
            ],
        },
        {
            "id": 5,
            "name": "Cloud Security",
            "group": "cybersecurity",
            "title_patterns": [r"cloud security", r"azure security", r"aws security", r"cloud security analyst", r"cloud security engineer"],
            "practical_skills": [
                "Microsoft Azure", "Azure security concepts", "Sentinel", "Terraform exposure",
                "Container security fundamentals", "Docker", "Kubernetes",
            ],
            "familiarity_skills": [
                "AWS fundamentals", "AWS CloudTrail", "Cloud IAM", "Cloud logging", "Cloud monitoring",
            ],
            "learning_targets": [
                "Azure RBAC administration", "Managed identities", "VNet/NSG/routing", "Storage security",
                "Key Vault", "Azure Policy", "Defender for Cloud", "AWS Config/GuardDuty", "CSPM",
            ],
        },
        {
            "id": 6,
            "name": "Application Security / Product Security",
            "group": "cybersecurity",
            "title_patterns": [r"appsec", r"application security", r"product security", r"secure code", r"software security"],
            "practical_skills": [
                "OWASP Top 10", "SQL Injection", "XSS", "Command Injection", "REST API security",
                "JWT security", "Authentication", "Authorization", "RBAC", "Input Validation",
                "Node.js", "Express.js", "MongoDB", "Structured Logging", "Secure SDLC fundamentals",
            ],
            "familiarity_skills": [
                "Java", "TypeScript", "JavaScript", "SQL",
            ],
            "learning_targets": [
                "Burp Suite", "OWASP ZAP", "SAST/DAST/SCA", "Semgrep", "SonarQube", "Snyk",
                "OWASP Dependency-Check", "Threat modeling", "Secure code review", "IDOR testing",
            ],
        },
        {
            "id": 7,
            "name": "Vulnerability Management / Security Testing",
            "group": "cybersecurity",
            "title_patterns": [r"vulnerability", r"vulnerability management", r"penetration test", r"pen test", r"security testing"],
            "practical_skills": [
                "Nessus", "Vulnerability scanning", "Vulnerability assessment", "Severity analysis",
                "Risk-based prioritization", "Remediation recommendations", "Vulnerability reporting",
            ],
            "familiarity_skills": [
                "CVE", "CVSS", "Patch management concepts", "Hardening", "Risk assessment",
            ],
            "learning_targets": [
                "Enterprise scanning", "CVSS v4", "EPSS", "CISA KEV", "Nmap", "Greenbone/OpenVAS",
                "Qualys", "Rapid7 InsightVM", "Metasploit", "Trivy", "Nikto",
            ],
        },
        {
            "id": 8,
            "name": "IAM / Identity and Access Management",
            "group": "cybersecurity",
            "title_patterns": [r"\biam\b", r"identity and access", r"access management", r"identity governance", r"identity analyst"],
            "practical_skills": [
                "IAM fundamentals", "Authentication", "Authorization", "RBAC", "Least privilege",
                "MFA", "Access control", "Okta practice",
            ],
            "familiarity_skills": [
                "Entra ID fundamentals", "Conditional Access concepts", "Identity security concepts",
            ],
            "learning_targets": [
                "Joiner/mover/leaver lifecycle", "Active Directory administration", "Group Policy",
                "Kerberos/NTLM", "LDAP/LDAPS", "SAML", "OAuth 2.0", "OIDC", "Entra PIM",
                "CyberArk", "SailPoint", "BloodHound CE",
            ],
        },
        {
            "id": 9,
            "name": "GRC / IT Risk / Compliance",
            "group": "cybersecurity",
            "title_patterns": [r"\bgrc\b", r"governance risk", r"compliance analyst", r"it risk", r"security compliance"],
            "practical_skills": [
                "Excel", "Security documentation", "Security reporting", "Framework knowledge",
            ],
            "familiarity_skills": [
                "NIST CSF", "CIS Controls", "ISO 27001 fundamentals", "Security risk concepts",
                "Policies and procedures", "Third-party risk fundamentals",
            ],
            "learning_targets": [
                "ISO 27001:2022 clauses", "Statement of Applicability", "Risk register",
                "Control testing", "ServiceNow IRM/GRC", "AuditBoard", "Vanta", "Drata",
                "OneTrust", "RSA Archer", "NIST SP 800-53",
            ],
        },
        {
            "id": 10,
            "name": "Security Research / Threat Intelligence",
            "group": "cybersecurity",
            "title_patterns": [r"threat intelligence", r"security research", r"\bcti\b", r"threat intel", r"threat hunter"],
            "practical_skills": [
                "Wireshark", "Sentinel", "Splunk", "Sysmon", "PowerShell", "Bash", "Nessus",
                "IOC/TTP analysis", "MITRE ATT&CK", "Threat hunting concepts", "OSINT concepts",
                "Phishing analysis", "Log analysis", "IOC extraction",
            ],
            "familiarity_skills": [
                "Mimecast practice", "Email security familiarity",
            ],
            "learning_targets": [
                "VirusTotal workflows", "AbuseIPDB", "URLhaus", "AlienVault OTX", "MISP",
                "OpenCTI", "GreyNoise", "STIX/TAXII", "YARA rule testing", "Static/dynamic malware analysis",
            ],
        },

        # --- PART B: IT SUPPORT AND INFRASTRUCTURE ROLE FAMILIES ---
        {
            "id": 11,
            "name": "Help Desk Technician",
            "group": "it_support_and_infrastructure",
            "title_patterns": [r"help desk", r"helpdesk", r"service desk", r"it helpdesk", r"help desk technician"],
            "practical_skills": [
                "Windows", "PowerShell", "Windows Event Logs", "Networking commands", "Documentation",
                "Root cause analysis",
            ],
            "familiarity_skills": [
                "DNS", "DHCP", "TCP/IP", "Authentication concepts", "Linux troubleshooting fundamentals",
            ],
            "learning_targets": [
                "ServiceNow ITSM", "Jira Service Management", "Freshservice", "ITIL concepts",
                "Ticket lifecycle", "Windows 11 user support", "Password resets", "Account lockouts",
                "Microsoft 365 user support", "Outlook/Teams troubleshooting", "Remote assistance (RDP, Quick Assist)",
            ],
        },
        {
            "id": 12,
            "name": "Desktop Support Engineer",
            "group": "it_support_and_infrastructure",
            "title_patterns": [r"desktop support", r"desktop engineer", r"field support", r"workstation support", r"hardware support"],
            "practical_skills": [
                "Windows troubleshooting", "PowerShell", "Event Viewer", "Hardware/software troubleshooting",
            ],
            "familiarity_skills": [
                "Network connectivity", "Endpoint security concepts",
            ],
            "learning_targets": [
                "Hardware component diagnosis", "CPU/RAM/motherboard/SSD troubleshooting",
                "BIOS/UEFI", "Windows installation/imaging", "WinRE boot recovery",
                "DISM", "SFC", "BSOD minidump analysis", "Intune", "BitLocker",
            ],
        },
        {
            "id": 13,
            "name": "IT Support Specialist",
            "group": "it_support_and_infrastructure",
            "title_patterns": [r"it support", r"it specialist", r"technical support engineer", r"tech support specialist", r"it operations"],
            "practical_skills": [
                "Windows", "Linux fundamentals", "PowerShell", "Sentinel", "Wireshark",
                "Security logs", "Endpoint investigation", "Documentation",
            ],
            "familiarity_skills": [
                "Networking", "DNS/DHCP/TCP/IP", "Authentication", "RBAC", "MFA", "Entra ID fundamentals",
            ],
            "learning_targets": [
                "ServiceNow ITSM", "Jira Service Management", "Active Directory Users & Computers",
                "Group Policy Management", "Intune enrollment", "Microsoft 365 tenant administration",
                "Exchange Online message trace", "Lansweeper",
            ],
        },
        {
            "id": 14,
            "name": "Junior System Administrator",
            "group": "it_support_and_infrastructure",
            "title_patterns": [r"system administrator", r"sysadmin", r"systems administrator", r"linux administrator", r"windows administrator", r"junior sysadmin"],
            "practical_skills": [
                "Windows", "Ubuntu/Linux", "PowerShell", "Bash", "Terraform exposure",
                "Docker", "Kubernetes fundamentals", "Nessus", "Splunk", "Sentinel",
                "Linux users/groups/permissions", "syslog", "journalctl",
            ],
            "familiarity_skills": [
                "DNS", "DHCP", "TCP/IP", "Azure/AWS fundamentals", "Monitoring and log analysis",
            ],
            "learning_targets": [
                "Windows Server 2022/2025", "Active Directory Domain Services", "Group Policy",
                "Linux server administration (systemd, APT/DNF)", "LVM", "Hyper-V", "VMware ESXi",
                "Zabbix", "PRTG", "Nagios", "Prometheus", "Grafana", "Ansible",
            ],
        },
        {
            "id": 15,
            "name": "Network Support Associate",
            "group": "it_support_and_infrastructure",
            "title_patterns": [r"network support", r"network associate", r"network engineer", r"network technician", r"noc engineer", r"noc analyst"],
            "practical_skills": [
                "Wireshark", "Nessus", "TCP/IP", "DNS", "DHCP", "HTTP/HTTPS", "TLS fundamentals",
                "Network traffic analysis", "Source/destination IP analysis", "Ports/protocols",
            ],
            "familiarity_skills": [
                "VPN fundamentals", "Firewalls", "IDS/IPS concepts", "Firewall log analysis",
                "Cisco/vendor familiarity", "Palo Alto/Fortinet/Check Point/Zscaler/Netskope familiarity",
            ],
            "learning_targets": [
                "Cisco Packet Tracer", "Cisco IOS CLI", "GNS3", "IPv4 subnetting/VLSM",
                "VLANs/802.1Q", "Routing (OSPF, BGP)", "STP", "NAT/PAT", "WPA2/WPA3 enterprise",
                "IPsec VPN", "SNMP", "NetFlow", "tcpdump", "SolarWinds",
            ],
        },
    ],
    "transferable_skills": [
        "Node.js", "Express.js", "MongoDB", "REST APIs", "JWT authentication",
        "RBAC", "Least privilege", "Input validation", "Structured logging",
        "Exception handling", "Troubleshooting", "Application log investigation",
        "Root cause analysis", "Python", "Bash", "Git", "Technical documentation",
    ],
}
