import logging
import os
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import Column, DateTime, Integer, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from bs4 import BeautifulSoup

# ——— Logging ———
logging.basicConfig(level=logging.DEBUG)

# ——— Database setup: SQLite by default ———
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./webaudit360.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Audit(Base):
    __tablename__ = "audits"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    raw_html = Column(Text)

# Create tables if they don’t exist
Base.metadata.create_all(bind=engine)

# ——— FastAPI setup ———
app = FastAPI(title="WebAudit360 Core")

class AuditRequest(BaseModel):
    url: HttpUrl

class AuditResponse(BaseModel):
    job_id: int
    url: HttpUrl

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Utility to prepare HTTPX client
def prepare_client():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/115.0 Safari/537.36"
        )
    }
    return httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers)

# Fetch HTML content
async def fetch_html(url: str) -> str:
    async with prepare_client() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

@app.post("/audit/", response_model=AuditResponse)
async def enqueue_audit(req: AuditRequest):
    db = None
    try:
        url_str = str(req.url)
        logging.debug(f"Starting fetch for URL: {url_str}")
        html = await fetch_html(url_str)

        db = SessionLocal()
        audit = Audit(url=url_str, raw_html=html)
        db.add(audit)
        db.commit()
        db.refresh(audit)

        logging.info(f"Stored audit id={audit.id} for URL: {url_str}")
        return AuditResponse(job_id=audit.id, url=req.url)

    except Exception as e:
        logging.exception("❌ Audit failed")
        raise HTTPException(status_code=502, detail=str(e))

    finally:
        if db:
            db.close()

@app.get("/results/{job_id}")
def get_result(job_id: int):
    db = SessionLocal()
    try:
        audit = db.query(Audit).filter(Audit.id == job_id).first()
        if not audit:
            raise HTTPException(status_code=404, detail="Job not found")

                # Parse HTML for basic metrics
        soup = BeautifulSoup(audit.raw_html, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        h1_count = len(soup.find_all('h1'))
        meta_desc_tag = soup.find('meta', attrs={'name': 'description'})
        meta_description = meta_desc_tag.get('content', '').strip() if meta_desc_tag else None

        # Additional counts
        img_count = len(soup.find_all('img'))
        link_count = len(soup.find_all('a'))

        result = {
            "job_id": audit.id,
            "url": audit.url,
            "fetched_at": audit.fetched_at.isoformat(),
            "title": title,
            "h1_count": h1_count,
            "meta_description": meta_description,
            "image_count": img_count,
            "link_count": link_count
        }
        return JSONResponse(content=result, status_code=200)

    finally:
        db.close()
