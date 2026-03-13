import os
import uuid
import json
import time
import logging
from logging.handlers import TimedRotatingFileHandler
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

DATA_DIR = os.environ.get("FILE_SHARE_DATA_DIR", "/app/data")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
LOG_DIR = os.environ.get("FILE_SHARE_LOG_DIR", "/app/logs")
LOG_FILE = os.path.join(LOG_DIR, "file_share.log")

MAX_FILE_SIZE = int(os.environ.get("FILE_SHARE_MAX_SIZE", 2 * 1024 * 1024 * 1024))
RETENTION_DAYS = int(os.environ.get("FILE_SHARE_RETENTION_DAYS", 30))
CLEANUP_TIME = os.environ.get("FILE_SHARE_CLEANUP_TIME", "03:00")
UPLOADS_PER_IP_DAILY = int(os.environ.get("FILE_SHARE_UPLOADS_LIMIT", 10))

try:
    cleanup_hour, cleanup_minute = map(int, CLEANUP_TIME.split(":"))
except ValueError:
    cleanup_hour, cleanup_minute = 3, 0

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("file_share")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

file_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",
    interval=1,
    backupCount=RETENTION_DAYS
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

app = FastAPI(title="Temporary File Sharing Service", docs_url="/docs", redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track uploads per IP per day
upload_tracker = defaultdict(lambda: {"count": 0, "last_reset": time.time()})

def reset_daily_counts():
    """Reset daily upload counts"""
    current_time = time.time()
    day_seconds = 24 * 60 * 60
    for ip, data in list(upload_tracker.items()):
        if current_time - data["last_reset"] >= day_seconds:
            upload_tracker[ip] = {"count": 0, "last_reset": current_time}
    logger.info("Daily upload counts reset")

def load_metadata():
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_metadata(metadata):
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f)

def cleanup_expired_files():
    logger.info("Running scheduled cleanup task")
    current_time = time.time()
    metadata = load_metadata()
    files_to_remove = []
    
    for file_uid, file_info in metadata.items():
        if "expiry" not in file_info:
            logger.warning(f"File {file_uid} has no expiry date, adding default expiry")
            file_info["expiry"] = (datetime.now() + timedelta(days=RETENTION_DAYS)).timestamp()
            continue
            
        if file_info["expiry"] < current_time:
            logger.info(f"Removing expired file: {file_uid}")
            file_path = os.path.join(DATA_DIR, file_uid)
            if os.path.exists(file_path):
                os.remove(file_path)
            files_to_remove.append(file_uid)
    
    for file_uid in files_to_remove:
        del metadata[file_uid]
    
    save_metadata(metadata)
    logger.info(f"Cleanup complete: {len(files_to_remove)} files removed")

def cleanup_old_logs():
    logger.info("Cleaning up old log files")
    cutoff_time = time.time() - (RETENTION_DAYS * 24 * 60 * 60)
    
    for filename in os.listdir(LOG_DIR):
        if filename.startswith("file_share.log."):
            file_path = os.path.join(LOG_DIR, filename)
            file_mtime = os.path.getmtime(file_path)
            if file_mtime < cutoff_time:
                logger.info(f"Removing old log file: {filename}")
                os.remove(file_path)

@app.on_event("startup")
def start_scheduler():
    try:
        metadata = load_metadata()
        current_time = time.time()
        modified = False
        
        for file_uid, file_info in list(metadata.items()):
            if "expiry" not in file_info:
                logger.warning(f"Repairing metadata for {file_uid}: adding missing expiry")
                file_info["expiry"] = (datetime.now() + timedelta(days=RETENTION_DAYS)).timestamp()
                modified = True
                
            if "upload_date" not in file_info:
                logger.warning(f"Repairing metadata for {file_uid}: adding missing upload_date")
                file_info["upload_date"] = current_time - (60 * 60 * 24)
                modified = True
                
            if "original_filename" not in file_info:
                logger.warning(f"Repairing metadata for {file_uid}: adding default filename")
                file_info["original_filename"] = f"file_{file_uid}"
                modified = True
                
            if "size" not in file_info:
                file_path = os.path.join(DATA_DIR, file_uid)
                if os.path.exists(file_path):
                    file_info["size"] = os.path.getsize(file_path)
                else:
                    file_info["size"] = 0
                modified = True
        
        if modified:
            save_metadata(metadata)
            logger.info("Metadata repaired and saved")
            
    except Exception as e:
        logger.error(f"Error checking metadata: {e}")
        if os.path.exists(METADATA_FILE):
            backup_file = f"{METADATA_FILE}.bak.{int(time.time())}"
            logger.warning(f"Backing up potentially corrupted metadata to {backup_file}")
            os.rename(METADATA_FILE, backup_file)
            save_metadata({})
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(cleanup_expired_files, 'cron', hour=cleanup_hour, minute=cleanup_minute)
    scheduler.add_job(cleanup_old_logs, 'cron', hour=cleanup_hour, minute=cleanup_minute + 10)
    scheduler.add_job(reset_daily_counts, 'cron', hour=0, minute=0)
    scheduler.start()
    logger.info(f"Scheduled cleanup task to run daily at {cleanup_hour:02d}:{cleanup_minute:02d}")
    logger.info(f"Upload limit set to {UPLOADS_PER_IP_DAILY} uploads per IP per day")
    
    cleanup_expired_files()

def get_client_ip(request: Request):
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.client.host
    return ip

def check_upload_limit(ip: str):
    current_time = time.time()
    day_seconds = 24 * 60 * 60
    
    # Reset count if it's been more than a day
    if current_time - upload_tracker[ip]["last_reset"] >= day_seconds:
        upload_tracker[ip] = {"count": 0, "last_reset": current_time}
    
    # Check if limit reached
    if upload_tracker[ip]["count"] >= UPLOADS_PER_IP_DAILY:
        raise HTTPException(
            status_code=429, 
            detail=f"Daily upload limit of {UPLOADS_PER_IP_DAILY} files reached. Try again tomorrow."
        )
    
    # Increment counter
    upload_tracker[ip]["count"] += 1
    return True

UPLOAD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>File Upload</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#0a0a0a;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#161616;border:1px solid #2a2a2a;border-radius:12px;padding:2.5rem;width:100%;max-width:480px;margin:1rem}
h1{font-size:1.4rem;font-weight:600;margin-bottom:1.5rem;color:#fff}
.drop{border:2px dashed #333;border-radius:8px;padding:3rem 1.5rem;text-align:center;cursor:pointer;transition:border-color .2s,background .2s}
.drop.over{border-color:#4a9eff;background:#4a9eff0a}
.drop p{color:#888;font-size:.95rem}
.drop .icon{font-size:2rem;margin-bottom:.5rem;display:block}
.file-name{margin-top:1rem;font-size:.85rem;color:#aaa;word-break:break-all}
input[type=file]{display:none}
button{width:100%;margin-top:1.2rem;padding:.75rem;background:#4a9eff;color:#fff;border:none;border-radius:8px;font-size:.95rem;font-weight:500;cursor:pointer;transition:opacity .2s}
button:disabled{opacity:.4;cursor:default}
button:hover:not(:disabled){opacity:.85}
.progress{margin-top:1rem;display:none}
.progress-bar{height:6px;background:#222;border-radius:3px;overflow:hidden}
.progress-fill{height:100%;width:0;background:#4a9eff;transition:width .3s}
.progress-text{font-size:.8rem;color:#888;margin-top:.4rem;text-align:right}
.result{margin-top:1.2rem;display:none;padding:1rem;background:#1a2a1a;border:1px solid #2a3a2a;border-radius:8px}
.result a{color:#4a9eff;word-break:break-all;text-decoration:none}
.result a:hover{text-decoration:underline}
.result .expiry{font-size:.8rem;color:#888;margin-top:.4rem}
.error{margin-top:1rem;color:#ff6b6b;font-size:.85rem;display:none}
footer{margin-top:1.5rem;text-align:center;font-size:.75rem;color:#555}
footer a{color:#666;text-decoration:none}
footer a:hover{color:#999}
</style>
</head>
<body>
<div class="card">
<h1>Upload a file</h1>
<div class="drop" id="drop">
  <span class="icon">&#8593;</span>
  <p>Drop a file here or click to browse</p>
  <div class="file-name" id="fileName"></div>
</div>
<input type="file" id="fileInput">
<button id="uploadBtn" disabled>Upload</button>
<div class="progress" id="progress">
  <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
  <div class="progress-text" id="progressText">0%</div>
</div>
<div class="error" id="error"></div>
<div class="result" id="result">
  <a id="link" href="#" target="_blank"></a>
  <div class="expiry" id="expiry"></div>
</div>
<footer><a href="/docs">API docs</a> &middot; <a href="https://github.com/hippiiee/Simple-python-file-sharing">GitHub</a></footer>
</div>
<script>
const drop=document.getElementById('drop'),fileInput=document.getElementById('fileInput'),
  fileName=document.getElementById('fileName'),uploadBtn=document.getElementById('uploadBtn'),
  progress=document.getElementById('progress'),progressFill=document.getElementById('progressFill'),
  progressText=document.getElementById('progressText'),result=document.getElementById('result'),
  link=document.getElementById('link'),expiry=document.getElementById('expiry'),
  error=document.getElementById('error');
let selectedFile=null;
drop.addEventListener('click',()=>fileInput.click());
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('over')});
drop.addEventListener('dragleave',()=>drop.classList.remove('over'));
drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('over');if(e.dataTransfer.files.length)pick(e.dataTransfer.files[0])});
fileInput.addEventListener('change',()=>{if(fileInput.files.length)pick(fileInput.files[0])});
function pick(f){selectedFile=f;fileName.textContent=f.name+' ('+fmt(f.size)+')';uploadBtn.disabled=false;result.style.display='none';error.style.display='none'}
function fmt(b){if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';if(b<1073741824)return(b/1048576).toFixed(1)+' MB';return(b/1073741824).toFixed(2)+' GB'}
uploadBtn.addEventListener('click',()=>{
  if(!selectedFile)return;
  const fd=new FormData();fd.append('file',selectedFile);
  const xhr=new XMLHttpRequest();
  xhr.open('POST','/upload/');
  uploadBtn.disabled=true;progress.style.display='block';error.style.display='none';result.style.display='none';
  xhr.upload.onprogress=e=>{if(e.lengthComputable){const p=Math.round(e.loaded/e.total*100);progressFill.style.width=p+'%';progressText.textContent=fmt(e.loaded)+' / '+fmt(e.total)+' ('+p+'%)'}};
  xhr.onload=()=>{
    progress.style.display='none';
    if(xhr.status===200){
      const r=JSON.parse(xhr.responseText);
      const url=location.origin+r.download_url;
      link.href=url;link.textContent=url;
      expiry.textContent='Expires: '+r.expiry_date;
      result.style.display='block';selectedFile=null;fileName.textContent='';
    } else {
      let msg='Upload failed';try{msg=JSON.parse(xhr.responseText).detail}catch(e){}
      error.textContent=msg;error.style.display='block';uploadBtn.disabled=false;
    }
  };
  xhr.onerror=()=>{progress.style.display='none';error.textContent='Network error';error.style.display='block';uploadBtn.disabled=false};
  xhr.send(fd);
});
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return UPLOAD_HTML

@app.post("/upload/")
async def upload_file(
    request: Request,
    file: UploadFile = File(...)
):
    client_ip = get_client_ip(request)
    check_upload_limit(client_ip)
    
    original_filename = os.path.basename(file.filename).strip()
    if not original_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_uid = str(uuid.uuid4())
    file_path = os.path.join(DATA_DIR, file_uid)

    total_size = 0
    chunk_size = 1024 * 1024  # 1MB chunks
    max_size_in_gb = MAX_FILE_SIZE / (1024 * 1024 * 1024)

    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    f.close()
                    os.remove(file_path)
                    logger.warning(f"File upload rejected - too large: >{total_size} bytes from {client_ip}")
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large, max size is {max_size_in_gb:.2f} GB"
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        logger.error(f"Upload error from {client_ip}: {e}")
        raise HTTPException(status_code=500, detail="Upload failed")

    metadata = load_metadata()
    expiry_time = (datetime.now() + timedelta(days=RETENTION_DAYS)).timestamp()

    metadata[file_uid] = {
        "original_filename": original_filename,
        "size": total_size,
        "upload_date": datetime.now().timestamp(),
        "expiry": expiry_time,
        "uploader_ip": client_ip
    }

    save_metadata(metadata)

    logger.info(f"File uploaded: {file_uid}, {original_filename}, {total_size} bytes from {client_ip}")

    return {
        "message": "File uploaded successfully",
        "download_url": f"/download/{file_uid}/{original_filename}",
        "file_uid": file_uid,
        "expiry_date": datetime.fromtimestamp(expiry_time).isoformat()
    }

@app.get("/download/{file_uid}/{original_filename}")
async def download_file(request: Request, file_uid: str, original_filename: str):
    client_ip = get_client_ip(request)
    metadata = load_metadata()
    
    if file_uid not in metadata:
        logger.warning(f"Download attempt for non-existent file: {file_uid} from {client_ip}")
        raise HTTPException(status_code=404, detail="File not found")
    
    if original_filename != metadata[file_uid]["original_filename"]:
        logger.warning(f"Download attempt with incorrect filename: {original_filename} for {file_uid} from {client_ip}")
        raise HTTPException(status_code=403, detail="Invalid filename")
        
    file_path = os.path.join(DATA_DIR, file_uid)
    
    if not os.path.exists(file_path):
        logger.warning(f"Download attempt for missing file: {file_uid} from {client_ip}")
        raise HTTPException(status_code=404, detail="File not found")
    
    logger.info(f"File download: {file_uid}, {original_filename} from {client_ip}")
        
    return FileResponse(
        path=file_path, 
        filename=original_filename,
        media_type="application/octet-stream"
    )

@app.get("/info/{file_uid}")
async def get_file_info(request: Request, file_uid: str):
    client_ip = get_client_ip(request)
    metadata = load_metadata()
    if file_uid not in metadata:
        logger.warning(f"Info request for non-existent file: {file_uid} from {client_ip}")
        raise HTTPException(status_code=404, detail="File not found")
    
    file_info = metadata[file_uid].copy()
    file_info["expiry_date"] = datetime.fromtimestamp(file_info["expiry"]).isoformat()
    file_info["upload_date_formatted"] = datetime.fromtimestamp(file_info["upload_date"]).isoformat()
    
    current_time = time.time()
    time_remaining_seconds = max(0, file_info["expiry"] - current_time)
    time_remaining_days = time_remaining_seconds / (60 * 60 * 24)
    
    file_info["time_remaining_days"] = round(time_remaining_days, 1)
    
    # Remove sensitive information
    if "uploader_ip" in file_info:
        del file_info["uploader_ip"]
    del file_info["expiry"]
    del file_info["upload_date"]
    
    logger.info(f"Info request: {file_uid} from {client_ip}")
    
    return file_info

@app.get("/config")
async def get_config(request: Request):
    client_ip = get_client_ip(request)
    logger.info(f"Config request from {client_ip}")
    
    # Get remaining uploads for this IP
    remaining = UPLOADS_PER_IP_DAILY
    if client_ip in upload_tracker:
        current_time = time.time()
        if current_time - upload_tracker[client_ip]["last_reset"] < 24 * 60 * 60:
            remaining = UPLOADS_PER_IP_DAILY - upload_tracker[client_ip]["count"]
    
    return {
        "max_file_size_bytes": MAX_FILE_SIZE,
        "max_file_size_mb": MAX_FILE_SIZE / (1024 * 1024),
        "max_file_size_gb": MAX_FILE_SIZE / (1024 * 1024 * 1024),
        "retention_days": RETENTION_DAYS,
        "cleanup_time": CLEANUP_TIME,
        "uploads_per_day_limit": UPLOADS_PER_IP_DAILY,
        "remaining_uploads_today": remaining
    }

@app.get("/stats")
async def get_stats(request: Request):
    client_ip = get_client_ip(request)
    logger.info(f"Stats request from {client_ip}")
    
    metadata = load_metadata()
    total_files = len(metadata)
    total_size = sum(info.get("size", 0) for info in metadata.values())
    
    return {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "total_size_mb": total_size / (1024 * 1024),
        "total_size_gb": total_size / (1024 * 1024 * 1024),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

