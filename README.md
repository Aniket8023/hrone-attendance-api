# Employee Attendance & Analytics API

A REST API built using Python, FastAPI, and MongoDB for employee management, attendance tracking, attendance regularization, and workforce analytics.

## Tech Stack

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo
- Pydantic

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer
- Git

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/Aniket8023/hrone-attendance-api.git
cd hrone-attendance-api
```

### 2. Create and activate a virtual environment

Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure MongoDB

Start your local MongoDB server and configure the following environment variables:

```text
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Keep these settings in a local `.env` file if the application supports loading it. Do not commit `.env` or any credentials.

### 5. Load sample data (optional)

If you want to test with the provided sample dataset, run:

```bash
python sample_seed.py
```

Run this only against a disposable development database because seeding may modify its data.

### 6. Start the application

```bash
uvicorn app.main:app --port 8000
```

## API Documentation

Once the server is running:

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- Health check: http://127.0.0.1:8000/health

## Main Features

- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance filtering and pagination
- Attendance regularization with audit history
- Employee monthly analytics
- Department attendance summaries
- Late-arrival leaderboard
- Department attendance trends
- MongoDB query execution statistics through explain endpoints

## Submission Notes

- Keep application code in `app/main.py`, as required by the assignment.
- Do not commit `.env`, credentials, virtual environments, or sensitive data.
- Refer to `openapi.yaml` and `DATA_MODEL.md` for the assignment contract and data model.
- See `REVIEW.md` and `DECISIONS.md` for the code review and implementation decisions.
