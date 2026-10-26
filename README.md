# Soultie — Northeast India, closer

A launch website and first-tester registration portal for Soultie, a Northeast India dating and community app. Built with React, Flask, and MongoDB.

## What's included

- Story-led, responsive launch page inspired by the supplied Rong app wireframes (brand name updated to Soultie).
- Admin-managed launch countdown.
- First-tester form collecting only name, email, and city.
- Admin login and tester list with CSV export.
- Flask REST API with MongoDB persistence.
- Seeded demo admin for local development.

The public story introduces vibe-first matching, Northeast cultures and languages, local discovery and highly rated nearby cafés, café booking, group hangouts, and women-focused safety features such as public meet-up suggestions, location sharing, emergency support, and report/block tools. These are presented as planned app features; actual in-app booking and safety services require their own integrations and operational setup.

## Requirements

- Python 3.10+
- Node.js 18+
- MongoDB running locally, or a MongoDB Atlas connection string

## Run locally

### 1. Start MongoDB

Use your local MongoDB service, or Docker:

```bash
docker run --name soultie-mongo -p 27017:27017 -d mongo:7
```

### 2. Configure and start Flask

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows PowerShell
# cp .env.example .env  # macOS/Linux
python seed_admin.py
python run.py
```

Edit `backend/.env` before use. The seed command creates a demo admin if no admin exists. Default local credentials are `admin@soultie.local` / `ChangeMe123!`. Change these immediately, and set a long random `SECRET_KEY` before deploying. The API runs at `http://localhost:5000`.

### 3. Start React

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL (usually `http://localhost:5173`). The admin page is at `/admin`.

## Admin use

Sign in at `/admin` with the configured admin email and password. The dashboard lists tester submissions and lets you set the public countdown date/time. A date in the past or no configured date shows a launch-ready state instead of a negative countdown. Tester CSV export is available from the dashboard.

## API routes

- `GET /api/health` — service health
- `GET /api/public/settings` — public countdown configuration
- `POST /api/testers` — register a tester (name, email, city only)
- `POST /api/admin/login` — admin login
- `GET /api/admin/testers` — protected tester list
- `GET /api/admin/testers.csv` — protected CSV export
- `PUT /api/admin/settings/countdown` — protected countdown update
- `POST /api/admin/logout` — end admin session

## Project structure

```text
soultie/
├── README.md
├── backend/
│   ├── app/__init__.py
│   ├── requirements.txt
│   ├── run.py
│   ├── seed_admin.py
│   └── .env.example
└── frontend/
    ├── package.json
    ├── index.html
    └── src/
```

## Before public deployment

Set unique admin credentials and a strong secret, configure HTTPS and production CORS, use a protected MongoDB deployment with backups, and add rate limiting and monitoring. The first-tester form deliberately stores only the three requested fields. The product positioning and app functions on the launch page describe Soultie's intended experience; they do not imply that third-party café booking, location, or emergency integrations are already connected.
