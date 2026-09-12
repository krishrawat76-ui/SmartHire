"""
Generate a synthetic JD + 18-resume corpus with a KNOWN expected ranking.

Why this exists: the real Sample_JD.pdf and 18 resumes are not on this machine yet.
Rather than block, we build a corpus whose failure modes are deliberately planted, so
calibration is meaningful rather than circular. Every document is rendered to a real
PDF so core/parser.py is genuinely exercised instead of bypassed.

When the real corpus arrives, drop it into data/ and re-run verification. This corpus
stays as a regression fixture.

    python scripts/make_synthetic_corpus.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _console  # noqa: F401  (configures stdout encoding on import)

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    try:
        import fitz
    except ImportError:
        sys.exit("PyMuPDF not installed. Activate .venv first.")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SYN = ROOT / "fixtures" / "synthetic"
EDGE = ROOT / "fixtures" / "edge"


# ─────────────────────────────────────────────────────────────────────────────
# The Job Description
#
# Modelled on the brief's "Junior Full Stack Developer Intern @ TechNova Solutions".
# Deliberately seeded with bias triggers ("5+ years" on an intern role, "rockstar",
# "young and energetic", "Tier-1 college") so bias_detector.py has real targets.
# ─────────────────────────────────────────────────────────────────────────────
JD = """TechNova Solutions
Junior Full Stack Developer Intern

About the Role
TechNova Solutions is looking for a young and energetic rockstar developer to join our
fast-paced product team. You will work across the stack, shipping features that reach
thousands of users. This is a hands-on internship with real ownership from week one.

Required Skills
- Strong proficiency in JavaScript and modern React for building user interfaces
- Must have experience with Node.js for server-side development
- Required: building and consuming REST APIs
- Solid understanding of HTML and CSS, including responsive layout
- Experience with a database such as MongoDB or SQL
- Version control with Git is required

Preferred Skills
- TypeScript is a strong plus
- Familiarity with Docker and containerised deployment is preferred
- Exposure to AWS or other cloud platforms is nice to have
- Unit testing with Jest or similar frameworks preferred
- Bonus: experience working in an Agile team

Requirements
- 5+ years experience building production web applications
- Candidates from Tier-1 college backgrounds strongly preferred
- Must be able to work from our Bangalore office five days a week
"""


# ─────────────────────────────────────────────────────────────────────────────
# The 18 candidates.
#
# `tier` is the human expectation, used by verification to check the engine agrees.
# `probe` marks candidates that exist to exercise a specific engine behaviour.
# ─────────────────────────────────────────────────────────────────────────────
CANDIDATES: list[dict] = [
    {
        "id": "c01", "name": "Aarav Mehta", "tier": "strong", "probe": None,
        "reason": "Textbook match — every required skill stated explicitly, plus most preferred.",
        "text": """Aarav Mehta
Bangalore, India | aarav.mehta@email.com | github.com/aaravm

EDUCATION
B.Tech Computer Science, 2022 - 2026

EXPERIENCE
Software Engineering Intern, Flipkart Internal Tools
Jun 2025 - Aug 2025
- Built responsive dashboards in React and TypeScript consumed by 400+ internal users
- Developed Node.js services exposing REST APIs for inventory reconciliation
- Wrote MongoDB aggregation pipelines to power analytics views
- Containerised the service with Docker and deployed to AWS ECS
- Added Jest unit tests, raising coverage from 31% to 78%

PROJECTS
CampusHub - Full stack event platform
- React frontend with server-side JavaScript backend on Node.js and Express
- MongoDB for persistence, JWT authentication, REST API consumed by a mobile client
- CI pipeline running Jest on every push, deployed via Docker

SKILLS
JavaScript, TypeScript, React, Node.js, Express, MongoDB, SQL, REST APIs,
Git, Docker, AWS, Jest, HTML, CSS, Agile
""",
    },
    {
        "id": "c02", "name": "Priya Nair", "tier": "strong", "probe": "HIDDEN_GEM",
        "reason": "Never writes 'Node.js' — says Express, Mongoose, server-side JavaScript. "
                  "Semantic channel must find her; lexical channel will not.",
        "text": """Priya Nair
Pune, India | priya.nair@email.com | github.com/priyanair-dev

EDUCATION
B.E. Information Technology, 2022 - 2026

EXPERIENCE
Backend Developer Intern, Zeta Payments
Jan 2025 - Jul 2025
- Designed and shipped RESTful API endpoints serving 12k requests per day
- Wrote server-side JavaScript using the Express framework and the Mongoose ODM
- Modelled transaction collections in MongoDB with compound indexes for query performance
- Implemented middleware for request validation, rate limiting and structured error handling
- Collaborated in two-week Agile sprints with daily standups

PROJECTS
LedgerLite - Expense tracking web application
- Single page interface built with React and hooks
- Backend written in Express with route handlers exposing a REST API
- MongoDB Atlas for storage, Mongoose schemas for validation
- Session handling with JSON Web Tokens

Bookmarks API
- Server-side JavaScript runtime handling async I/O for a bookmarking service
- Middleware chain for authentication, CORS and logging

SKILLS
JavaScript, React, Express, Mongoose, MongoDB, REST APIs, Git, HTML, CSS,
Agile, Postman, JWT
""",
    },
    {
        "id": "c03", "name": "Sneha Kulkarni", "tier": "medium", "probe": "SURFACE_MATCH",
        "reason": "Keyword dump with no supporting substance. Lexical channel will overrate her; "
                  "semantic channel must not.",
        "text": """Sneha Kulkarni
Mumbai, India | sneha.k@email.com | github.com/snehak

SKILLS
JavaScript, React, Node.js, Express, MongoDB, SQL, REST APIs, Git, Docker, AWS,
Kubernetes, GraphQL, TypeScript, Jest, HTML, CSS, Agile, CI/CD, Redis, Kafka,
Microservices, Terraform, Jenkins, Webpack, Redux, Next.js, PostgreSQL

EDUCATION
B.Sc Computer Applications, 2022 - 2025

EXPERIENCE
Intern, Local Startup
May 2024 - Jun 2024
- Assisted the team
- Attended meetings and learned about the software development lifecycle

INTERESTS
Technology, reading, cricket
""",
    },
    {
        "id": "c04", "name": "Ananya Iyer", "tier": "strong", "probe": "TYPO_CASE",
        "reason": "Misspellings and spacing variants throughout. Alias map + RapidFuzz must recover them.",
        "text": """Ananya Iyer
Chennai, India | ananya.iyer@email.com

EDUCATION
B.Tech Computer Science and Engineering, 2021 - 2025

EXPERIENCE
Full Stack Developer Intern, Freshworks
Feb 2025 - Aug 2025
- Built customer-facing screens in React.js with reusable component library
- Developed backend services on Node JS exposing REST Api endpoints
- Used Mongo DB for document storage and Postgres for relational reporting
- Version control with git, pull request reviews, feature branch workflow
- Wrote unit tests using the Jest framework

PROJECTS
StudySync - Collaborative notes application
- Frontend in Reactjs with responsive HTML and CSS
- Backend in Javscript running on Node JS with Express
- Real time sync over websockets

SKILLS
Javscript, Typescript, React.js, Node JS, Express, Mongo DB, Postgres,
REST Api, git, Docker, HTML, CSS, Jest
""",
    },
    {
        "id": "c05", "name": "Karthik Reddy", "tier": "medium", "probe": "MESSY_FORMAT",
        "reason": "No section headers, run-on prose, wildly inconsistent date formats. "
                  "Parser must fall back to whole-document chunking without breaking.",
        "text": """Karthik Reddy chennai karthik.reddy@email.com +91 98765 43210
i am a final year computer science student graduating 2026 who has spent the last
two years building web applications mostly with javascript. from 01/2024-06/2024 i
interned at a logistics startup where i wrote react components for their tracking
dashboard and helped connect them to rest apis written by the backend team. later
Summer 2024 i worked on a side project called routeplan which used node and express
with a mongodb database to store delivery routes, i deployed it myself using docker
on a digitalocean droplet. during 2023-24 i also did freelance work building small
business websites with html css and some javascript, handled git for all of these
projects and got comfortable with branching and merging. i have used sql lightly
mostly sqlite for small things. currently learning typescript and writing tests
with jest. comfortable working in a team, did agile sprints at the logistics startup.
""",
    },
    {
        "id": "c06", "name": "Arjun Pillai", "tier": "weak", "probe": "NEAR_EMPTY",
        "reason": "~40 characters. Exercises the P5/P95 outlier defence — must not compress the pool.",
        "text": "Arjun Pillai\nStudent\narjun@email.com\n",
    },
    {
        "id": "c07", "name": "Divya Sharma", "tier": "strong", "probe": None,
        "reason": "Strong MERN with deployment depth.",
        "text": """Divya Sharma
Hyderabad, India | divya.sharma@email.com | linkedin.com/in/divyasharma | github.com/divyasharma

EDUCATION
B.Tech Information Technology, 2022 - 2026

EXPERIENCE
Full Stack Intern, Darwinbox
Jun 2025 - Nov 2025
- Shipped six features end to end across a React and Node.js codebase
- Built REST APIs in Express backed by MongoDB, handling 50k daily requests
- Migrated legacy class components to React hooks and TypeScript
- Dockerised three services and wrote the GitHub Actions pipeline deploying them to AWS
- Maintained Jest and React Testing Library suites

PROJECTS
MediTrack - Clinic management system
- React frontend, Node.js and Express backend, MongoDB persistence
- Role based access control, REST API, responsive HTML and CSS
- Deployed with Docker Compose

SKILLS
JavaScript, TypeScript, React, Node.js, Express, MongoDB, PostgreSQL, REST APIs,
Git, GitHub Actions, Docker, AWS, Jest, HTML, CSS, Agile
""",
    },
    {
        "id": "c08", "name": "Aditya Kumar", "tier": "medium", "probe": None,
        "reason": "Strong backend, no React at all. Tests that a partial-stack candidate ranks mid.",
        "text": """Aditya Kumar
Delhi, India | aditya.kumar@email.com

EDUCATION
B.Tech Computer Science, 2021 - 2025

EXPERIENCE
Backend Engineering Intern, Paytm
Mar 2025 - Sep 2025
- Built Node.js microservices exposing REST APIs for the merchant settlement flow
- Optimised PostgreSQL queries, cutting p95 latency from 840ms to 190ms
- Wrote Express middleware for authentication and audit logging
- Containerised services with Docker, deployed on AWS EKS
- Participated in Agile ceremonies and code review

PROJECTS
QueueRunner - Distributed job queue
- Node.js worker pool with Redis-backed queue
- REST API for job submission and status polling

SKILLS
JavaScript, Node.js, Express, PostgreSQL, SQL, REST APIs, Redis, Git, Docker,
AWS, Linux, Agile
""",
    },
    {
        "id": "c09", "name": "Meera Joshi", "tier": "medium", "probe": None,
        "reason": "Frontend only, no backend. Mirror image of Aditya.",
        "text": """Meera Joshi
Bangalore, India | meera.joshi@email.com | dribbble.com/meeraj

EDUCATION
B.Des Interaction Design, 2022 - 2026

EXPERIENCE
Frontend Developer Intern, Swiggy Design Systems
Apr 2025 - Oct 2025
- Built and documented 24 React components for the internal design system
- Implemented responsive layouts in HTML and CSS across four breakpoints
- Improved Lighthouse accessibility score from 71 to 96
- Worked closely with designers in Figma to translate specs into JavaScript components
- Used Git for version control with a trunk-based workflow

PROJECTS
Portfolio site
- Static site in React with CSS animations and responsive grid layout

SKILLS
JavaScript, React, HTML, CSS, Sass, Figma, Git, Accessibility, Responsive Design
""",
    },
    {
        "id": "c10", "name": "Ishita Ghosh", "tier": "strong", "probe": None,
        "reason": "Strong modern stack, slightly thinner on database work.",
        "text": """Ishita Ghosh
Kolkata, India | ishita.ghosh@email.com

EDUCATION
B.Tech Computer Science, 2022 - 2026

EXPERIENCE
Software Intern, Postman
May 2025 - Nov 2025
- Built React interfaces in TypeScript for the API documentation product
- Wrote Node.js services exposing REST APIs consumed by the web client
- Authored GraphQL resolvers over an existing REST layer
- Maintained Jest test suites and set up Playwright end-to-end coverage
- Reviewed pull requests and worked in two-week Agile sprints

PROJECTS
SpecViewer - OpenAPI specification browser
- React and TypeScript frontend, Node.js backend, REST API
- Responsive HTML and CSS, dark mode support

SKILLS
JavaScript, TypeScript, React, Node.js, REST APIs, GraphQL, Git, Jest,
Playwright, HTML, CSS, Agile
""",
    },
    {
        "id": "c11", "name": "Rohan Desai", "tier": "medium", "probe": None,
        "reason": "React and SQL, no Node. Solid but incomplete.",
        "text": """Rohan Desai
Ahmedabad, India | rohan.desai@email.com

EDUCATION
B.E. Computer Engineering, 2021 - 2025

EXPERIENCE
Web Developer Intern, Infibeam
Jul 2024 - Mar 2025
- Built product listing and checkout screens in React
- Consumed REST APIs provided by the platform team
- Wrote SQL queries against MySQL for reporting dashboards
- Maintained responsive HTML and CSS templates
- Git based workflow with feature branches

PROJECTS
ShopLocal - Marketplace prototype
- React frontend consuming a third party REST API
- MySQL database schema design and query optimisation

SKILLS
JavaScript, React, HTML, CSS, SQL, MySQL, REST APIs, Git, Bootstrap, jQuery
""",
    },
    {
        "id": "c12", "name": "Vikram Singh", "tier": "medium", "probe": None,
        "reason": "Right shape of work, wrong stack — Python/Django instead of JS. "
                  "Semantic channel should see the adjacency; lexical should not overrate.",
        "text": """Vikram Singh
Jaipur, India | vikram.singh@email.com | github.com/vikramsingh-py

EDUCATION
B.Tech Computer Science, 2021 - 2025

EXPERIENCE
Backend Intern, Zomato
Jun 2024 - Feb 2025
- Built REST APIs in Django REST Framework for the restaurant partner portal
- Designed PostgreSQL schemas and wrote migrations
- Implemented Celery tasks for asynchronous report generation
- Deployed with Docker and monitored through Grafana
- Version control with Git, reviewed in GitLab merge requests

PROJECTS
FormFlow - Dynamic form builder
- Django backend exposing a REST API
- Server rendered HTML templates with CSS, light JavaScript for interactivity

SKILLS
Python, Django, REST APIs, PostgreSQL, SQL, Celery, Docker, Git, HTML, CSS,
Linux, Agile
""",
    },
    {
        "id": "c13", "name": "Rahul Menon", "tier": "medium", "probe": None,
        "reason": "Java backend. Adjacent but further from the JD than Vikram.",
        "text": """Rahul Menon
Kochi, India | rahul.menon@email.com | github.com/rahulmenon

EDUCATION
B.Tech Information Technology, 2021 - 2025

EXPERIENCE
Software Intern, UST Global
Aug 2024 - Apr 2025
- Developed REST endpoints in Java using Spring Boot for an insurance claims system
- Wrote SQL queries and stored procedures against Oracle
- Built JUnit test coverage for the service layer
- Used Git and Jenkins for version control and continuous integration
- Worked in Agile sprints with a distributed team

SKILLS
Java, Spring Boot, REST APIs, SQL, Oracle, Hibernate, JUnit, Git, Jenkins,
Maven, Agile
""",
    },
    {
        "id": "c14", "name": "Nisha Verma", "tier": "weak", "probe": None,
        "reason": "Data science with a little Flask. Mostly off-target.",
        "text": """Nisha Verma
Indore, India | nisha.verma@email.com

EDUCATION
M.Sc Data Science, 2023 - 2025

EXPERIENCE
Data Science Intern, Mu Sigma
Jan 2025 - Jul 2025
- Built churn prediction models in Python using scikit-learn and XGBoost
- Cleaned and joined datasets with pandas across 14 source tables
- Wrote SQL queries against Snowflake for feature extraction
- Exposed one model behind a small Flask REST API for the product team
- Presented findings to stakeholders in weekly reviews

SKILLS
Python, pandas, NumPy, scikit-learn, XGBoost, SQL, Snowflake, Flask,
Matplotlib, Jupyter, Statistics
""",
    },
    {
        "id": "c15", "name": "Pooja Rane", "tier": "weak", "probe": None,
        "reason": "Mobile developer. Some REST and Git overlap, little else.",
        "text": """Pooja Rane
Nagpur, India | pooja.rane@email.com

EDUCATION
B.E. Computer Science, 2021 - 2025

EXPERIENCE
Android Developer Intern, Practo
Sep 2024 - May 2025
- Built appointment booking screens in Kotlin using Jetpack Compose
- Consumed REST APIs with Retrofit and handled offline caching with Room
- Implemented Material Design components and responsive layouts
- Git based workflow, code review through Bitbucket

SKILLS
Kotlin, Java, Android, Jetpack Compose, Retrofit, REST APIs, Room, SQLite,
Git, Material Design
""",
    },
    {
        "id": "c16", "name": "Tanvi Bhatt", "tier": "weak", "probe": None,
        "reason": "Early student, basic frontend only.",
        "text": """Tanvi Bhatt
Surat, India | tanvi.bhatt@email.com

EDUCATION
B.Sc Computer Science, 2024 - 2027 (in progress)

PROJECTS
Personal portfolio website
- Built with HTML and CSS, responsive on mobile and desktop
- Small amount of JavaScript for a navigation menu toggle

Recipe finder page
- Static HTML page that filters a hardcoded list with JavaScript

COURSEWORK
Introduction to Programming, Data Structures, Web Technologies

SKILLS
HTML, CSS, basic JavaScript, Git (learning), Microsoft Office
""",
    },
    {
        "id": "c17", "name": "Siddharth Rao", "tier": "weak", "probe": None,
        "reason": "Mechanical engineering. Near-zero overlap — anchors the bottom of the pool.",
        "text": """Siddharth Rao
Coimbatore, India | siddharth.rao@email.com

EDUCATION
B.Tech Mechanical Engineering, 2021 - 2025

EXPERIENCE
Design Intern, Ashok Leyland
Jun 2024 - Dec 2024
- Created 3D assemblies in SolidWorks for chassis subcomponents
- Ran finite element analysis in ANSYS to validate load tolerances
- Prepared manufacturing drawings to GD&T standards
- Coordinated with the production floor on tolerance issues

SKILLS
SolidWorks, ANSYS, AutoCAD, CATIA, GD&T, MATLAB, Thermodynamics,
Manufacturing Processes
""",
    },
    {
        "id": "c18", "name": "Manav Shah", "tier": "weak", "probe": None,
        "reason": "Business/marketing. Second anchor at the bottom.",
        "text": """Manav Shah
Mumbai, India | manav.shah@email.com

EDUCATION
BBA Marketing, 2022 - 2025

EXPERIENCE
Marketing Intern, Nykaa
Feb 2025 - Aug 2025
- Managed social media calendar across four channels
- Built campaign creatives in Canva and Figma
- Analysed campaign performance in Google Analytics and Excel
- Coordinated with the web team on landing page copy

SKILLS
Content Marketing, SEO, Google Analytics, Canva, Figma, Excel,
Social Media Strategy, Copywriting
""",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# PDF rendering
# ─────────────────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = 595, 842          # A4 points
MARGIN, LEADING, FONTSIZE = 56, 13.6, 9.6
MAX_CHARS = 92                      # soft wrap width


def _wrap(line: str, width: int = MAX_CHARS) -> list[str]:
    if len(line) <= width:
        return [line]
    out, cur = [], ""
    for word in line.split(" "):
        if len(cur) + len(word) + 1 > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return out


def render_pdf(text: str, dest: pathlib.Path) -> None:
    """Render plain text to a real, text-layer PDF."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    for raw in text.splitlines():
        for line in (_wrap(raw) if raw.strip() else [""]):
            if y > PAGE_H - MARGIN:
                page = doc.new_page(width=PAGE_W, height=PAGE_H)
                y = MARGIN
            if line:
                page.insert_text((MARGIN, y), line, fontsize=FONTSIZE, fontname="helv")
            y += LEADING
    doc.save(dest)
    doc.close()


def render_image_only_pdf(text: str, dest: pathlib.Path) -> None:
    """A scanned-looking PDF with NO text layer — forces the parser's degraded path."""
    src = fitz.open()
    page = src.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    for raw in text.splitlines():
        for line in (_wrap(raw) if raw.strip() else [""]):
            if line:
                page.insert_text((MARGIN, y), line, fontsize=FONTSIZE, fontname="helv")
            y += LEADING
    pix = page.get_pixmap(dpi=110)
    src.close()

    out = fitz.open()
    p = out.new_page(width=PAGE_W, height=PAGE_H)
    p.insert_image(fitz.Rect(0, 0, PAGE_W, PAGE_H), pixmap=pix)
    out.save(dest)
    out.close()


def main() -> None:
    SYN.mkdir(parents=True, exist_ok=True)
    EDGE.mkdir(parents=True, exist_ok=True)

    # --- JD ---
    (SYN / "jd_technova.txt").write_text(JD, encoding="utf-8")
    render_pdf(JD, SYN / "jd_technova.pdf")

    # --- 18 resumes ---
    manifest = {
        "generated_by": "scripts/make_synthetic_corpus.py",
        "note": "Synthetic corpus with a known expected ranking. Replace with the real "
                "18 resumes when they arrive; keep this as a regression fixture.",
        "jd": "jd_technova.pdf",
        "candidates": [],
    }
    for c in CANDIDATES:
        stem = f"{c['id']}_{c['name'].lower().replace(' ', '_')}"
        (SYN / f"{stem}.txt").write_text(c["text"], encoding="utf-8")
        render_pdf(c["text"], SYN / f"{stem}.pdf")
        manifest["candidates"].append({
            "id": c["id"], "name": c["name"], "file": f"{stem}.pdf",
            "expected_tier": c["tier"], "probe": c["probe"], "reason": c["reason"],
        })

    (SYN / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # --- Edge cases for the resilience fuzz test ---
    render_image_only_pdf(CANDIDATES[0]["text"], EDGE / "scanned_no_text_layer.pdf")

    blank = fitz.open()
    blank.new_page(width=PAGE_W, height=PAGE_H)
    blank.save(EDGE / "empty.pdf")
    blank.close()

    good = (SYN / "c01_aarav_mehta.pdf").read_bytes()
    (EDGE / "corrupted.pdf").write_bytes(good[: len(good) // 3])
    (EDGE / "not_really_a_pdf.pdf").write_bytes(b"This is plain text pretending to be a PDF.\n")

    n_strong = sum(1 for c in CANDIDATES if c["tier"] == "strong")
    n_med = sum(1 for c in CANDIDATES if c["tier"] == "medium")
    n_weak = sum(1 for c in CANDIDATES if c["tier"] == "weak")
    print(f"JD + {len(CANDIDATES)} resumes written to {SYN}")
    print(f"  expected spread: {n_strong} strong / {n_med} medium / {n_weak} weak")
    print(f"  probes: " + ", ".join(f"{c['id']}={c['probe']}" for c in CANDIDATES if c["probe"]))
    print(f"4 edge-case PDFs written to {EDGE}")


if __name__ == "__main__":
    main()
