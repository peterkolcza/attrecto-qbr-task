"""Demo seed data — pre-loads project context for the dashboard.

Creates baseline project state so the evaluator sees a populated dashboard
before running email analysis.
"""

from __future__ import annotations


def get_demo_projects() -> list[dict]:
    """Return pre-seeded project data for the dashboard demo."""
    return [
        {
            "name": "Project Phoenix",
            "pm": "Péter Kovács (kovacs.peter@kisjozsitech.hu)",
            "team_size": 5,
            "status": "active",
            "health": "unknown",  # will be updated after email analysis
            "qbr_date": "2025-07-15",
            "q3_focus": "Login/registration module, UI polish, client demo feedback",
            "known_risks": "SSO scope clarification pending, CI/CD pipeline setup delayed",
            "email_threads": 6,
            "team": [
                {"name": "Péter Kovács", "role": "PM"},
                {"name": "Zsuzsa Varga", "role": "BA"},
                {"name": "István Nagy", "role": "Senior Dev"},
                {"name": "Anna Kiss", "role": "Frontend Dev"},
                {"name": "Gábor Horváth", "role": "Junior Dev"},
            ],
        },
        {
            "name": "Project Omicron",
            "pm": "Gábor Nagy (gabor.nagy@kisjozsitech.hu)",
            "team_size": 6,
            "status": "active",
            "health": "unknown",
            "qbr_date": "2025-07-15",
            "q3_focus": "User profile, product list, payment gateway, client onboarding",
            "known_risks": "Production login outage occurred, report export requested by client",
            "email_threads": 6,
            "team": [
                {"name": "Gábor Nagy", "role": "PM"},
                {"name": "Eszter Varga", "role": "BA"},
                {"name": "Péter Kovács", "role": "Senior Dev"},
                {"name": "Bence Tóth", "role": "Medior Dev"},
                {"name": "Anna Horváth", "role": "Junior Dev"},
                {"name": "Zoltán Kiss", "role": "AM"},
            ],
        },
        {
            "name": "DivatKirály",
            "pm": "Péter Kovács (peter.kovacs@kisjozsitech.hu)",
            "team_size": 6,
            "status": "active",
            "health": "unknown",
            "qbr_date": "2025-07-15",
            "q3_focus": "Webshop homepage, payment integration, registration, search",
            "known_risks": "Payment gateway API doc mismatch, GDPR checkbox compliance",
            "email_threads": 6,
            "team": [
                {"name": "Péter Kovács", "role": "PM"},
                {"name": "Anna Nagy", "role": "BA"},
                {"name": "Gábor Kiss", "role": "Backend Dev"},
                {"name": "Bence Szabó", "role": "Frontend Dev"},
                {"name": "Zsófia Varga", "role": "Full-stack Dev"},
                {"name": "Eszter Horváth", "role": "Client RM"},
            ],
        },
    ]
