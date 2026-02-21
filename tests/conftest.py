"""Shared test fixtures for ApplyPilot test suite."""

import sqlite3
import pytest

from applypilot.database import init_db, get_connection


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with the full schema."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def mock_profile():
    """Return a realistic test profile."""
    return {
        "personal": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "555-0100",
            "linkedin_url": "https://linkedin.com/in/janedoe",
            "github_url": "https://github.com/janedoe",
        },
        "skills_boundary": {
            "languages": ["Python", "JavaScript", "TypeScript", "SQL"],
            "frameworks": ["React", "FastAPI", "Django", "Node.js"],
            "devops": ["Docker", "Kubernetes", "AWS", "Terraform"],
            "databases": ["PostgreSQL", "Redis", "MongoDB"],
            "tools": ["Git", "CI/CD", "Jira"],
        },
        "resume_facts": {
            "preserved_companies": ["Acme Corp", "TechStart Inc"],
            "preserved_projects": ["DataPipeline", "APIGateway"],
            "preserved_school": "MIT",
            "real_metrics": ["reduced latency 40%", "processed 1M events/day"],
        },
        "experience": {
            "years_of_experience_total": 5,
            "education_level": "B.S. Computer Science",
            "current_job_title": "Software Engineer",
            "current_company": "Acme Corp",
        },
    }


@pytest.fixture
def mock_resume_text():
    """Return a realistic test resume text."""
    return """Jane Doe
Software Engineer
jane@example.com | 555-0100 | github.com/janedoe | linkedin.com/in/janedoe

SUMMARY
Full-stack engineer with 5 years of experience building scalable web applications and data pipelines.

TECHNICAL SKILLS
Languages: Python, JavaScript, TypeScript, SQL
Frameworks: React, FastAPI, Django, Node.js
DevOps & Infra: Docker, Kubernetes, AWS, Terraform
Databases: PostgreSQL, Redis, MongoDB
Tools: Git, CI/CD, Jira

EXPERIENCE
Senior Software Engineer at Acme Corp
Python, React, AWS | 2022-Present
- Built real-time data pipeline processing 1M events/day using Python and Kafka
- Reduced API latency 40% by optimizing database queries and adding Redis caching
- Designed microservices architecture serving 500K daily active users
- Automated CI/CD pipeline reducing deployment time from 2 hours to 15 minutes

Software Engineer at TechStart Inc
JavaScript, Node.js, PostgreSQL | 2019-2022
- Developed RESTful APIs handling 10K requests/second
- Implemented automated testing suite achieving 95% code coverage
- Led migration from monolith to microservices architecture

PROJECTS
DataPipeline - Real-time data processing framework
Python, Kafka, PostgreSQL | 2023
- Built streaming ETL pipeline processing 500K records per hour
- Implemented fault-tolerant message handling with dead letter queues

APIGateway - API management platform
TypeScript, Node.js, Redis | 2022
- Designed rate limiting and authentication middleware
- Reduced API response times by 60% with intelligent caching

EDUCATION
MIT | B.S. Computer Science"""


@pytest.fixture
def mock_job():
    """Return a realistic test job dict."""
    return {
        "url": "https://example.com/jobs/123",
        "title": "Senior Software Engineer",
        "site": "ExampleCorp",
        "location": "San Francisco, CA (Remote)",
        "full_description": (
            "We are looking for a Senior Software Engineer to join our platform team. "
            "Requirements: 5+ years of experience with Python, JavaScript, and cloud services. "
            "Experience with Docker, Kubernetes, and CI/CD pipelines. "
            "Strong knowledge of PostgreSQL and Redis. "
            "Experience building RESTful APIs and microservices. "
            "Nice to have: Experience with React, TypeScript, and Terraform."
        ),
        "fit_score": 8,
        "application_url": "https://example.com/apply/123",
    }
