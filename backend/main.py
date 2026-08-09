from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from services.pipeline import SUPPORTED_EXTENSIONS, process_report
from services.supabase_client import (
    create_report,
    delete_extraction,
    delete_report,
    get_all_extractions,
    get_extraction_detail,
    get_recent_reports,
    get_report,
)

app = FastAPI(title="Document Pipeline API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """Queue a filing for processing. Returns immediately with a report_id to poll.

    Validation is on the extension rather than the content type, matching how the
    pipeline dispatches: EDGAR primary documents are HTML, and clients disagree
    about what MIME type to send for them.
    """
    if not file.filename or not file.filename.lower().endswith(SUPPORTED_EXTENSIONS):
        raise HTTPException(
            400, f"Unsupported file type. Accepted: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Uploaded file is empty")

    report = create_report(file.filename)
    background_tasks.add_task(process_report, report["id"], file.filename, file_bytes)

    return {"status": "processing", "report_id": report["id"]}


@app.get("/api/extractions")
def list_extractions():
    return get_all_extractions()


@app.get("/api/extractions/{extraction_id}")
def extraction_detail(extraction_id: str):
    return get_extraction_detail(extraction_id)


@app.delete("/api/extractions/{extraction_id}")
def remove_extraction(extraction_id: str):
    result = delete_extraction(extraction_id)
    if not result.get("deleted"):
        raise HTTPException(404, "Extraction not found")
    return result


@app.get("/api/reports")
def list_reports(limit: int = 20):
    return get_recent_reports(limit=limit)


@app.get("/api/reports/{report_id}")
def report_detail(report_id: str):
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


@app.delete("/api/reports/{report_id}")
def remove_report(report_id: str):
    result = delete_report(report_id)
    if not result.get("deleted"):
        raise HTTPException(404, "Report not found")
    return result


@app.get("/")
def root():
    return {"status": "ok", "service": "document-pipeline"}
