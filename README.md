# Simple Python File Sharing Service

<p align="center">
  A simple and configurable temporary file sharing service.
  <br>
  <a href="https://twitter.com/intent/follow?screen_name=hiippiiie" title="Follow"><img src="https://img.shields.io/twitter/follow/hiippiiie?label=hiippiiie&style=social"></a>
  <img alt="Docker Ready" src="https://img.shields.io/badge/docker-ready-blue.svg">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green.svg">
  <br>
</p>

I needed a basic file sharing service for my personal use, so I created this project :)

## Features

- [x] Temporary file sharing with automatic expiration
- [x] Configurable file retention period (default 30 days)
- [x] Configurable file size limits (default 2GB)
- [x] IP-based rate limiting to prevent abuse (default 10 uploads/day)
- [x] Automatic file cleanup at configurable times
- [x] Simple API

## Installation
## Configuration

The service can be configured using environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `FILE_SHARE_DATA_DIR` | Directory to store files | `/app/data` |
| `FILE_SHARE_LOG_DIR` | Directory to store logs | `/app/logs` |
| `FILE_SHARE_MAX_SIZE` | Maximum file size in bytes | 2GB |
| `FILE_SHARE_RETENTION_DAYS` | Days to keep files | 30 |
| `FILE_SHARE_CLEANUP_TIME` | Daily cleanup time (HH:MM) | 03:00 |
| `FILE_SHARE_UPLOADS_LIMIT` | Maximum uploads per IP per day | 10 |

```bash
docker compose up
```


## API Usage

Once running, the API will be available at `http://localhost:8000/`.

## Documentation

API docs are available at:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc


### Upload a File

```bash
curl -X POST http://localhost:8000/upload/ -F "file=@yourfile.txt"
```

Example response:
```json
{
  "message": "File uploaded successfully",
  "download_url": "/download/a1b2c3d4-e5f6-g7h8-i9j0/yourfile.txt",
  "file_uid": "a1b2c3d4-e5f6-g7h8-i9j0",
  "expiry_date": "2025-04-15T00:00:00"
}
```

### Download a File

The download URL requires both the file ID and the original filename:

```bash
curl -O http://localhost:8000/download/a1b2c3d4-e5f6-g7h8-i9j0/yourfile.txt
```

### Get File Information

```bash
curl http://localhost:8000/info/a1b2c3d4-e5f6-g7h8-i9j0
```

Example response:
```json
{
  "original_filename": "yourfile.txt",
  "size": 1024,
  "upload_date_formatted": "2023-05-15T12:30:45",
  "expiry_date": "2023-06-15T12:30:45",
  "time_remaining_days": 29.5
}
```

### Get Configuration

```bash
curl http://localhost:8000/config
```

Example response:
```json
{
  "max_file_size_bytes": 2147483648,
  "max_file_size_mb": 2048,
  "max_file_size_gb": 2,
  "retention_days": 30,
  "cleanup_time": "03:00",
  "uploads_per_day_limit": 10,
  "remaining_uploads_today": 8
}
```

### Get Statistics

```bash
curl http://localhost:8000/stats
```

Example response:
```json
{
  "total_files": 5,
  "total_size_bytes": 10485760,
  "total_size_mb": 10,
  "total_size_gb": 0.01
}
```

## How it Works

- **File Upload**: Files are saved with a random UUID as the filename
- **Metadata Storage**: File information is stored in a JSON metadata file
- **Expiration**: Files are automatically removed after the retention period
- **Download**: Original filename must be provided for download
- **Rate Limiting**: IP-based upload limiting prevents abuse

## Extra

I personnally use this great alias for quick upload (you need jq):
```
alias upload='f(){
  BASE_URL="http://localhost:8000"
  RESPONSE=$(curl -s -F "file=@$1" "$BASE_URL/upload/")
  DOWNLOAD_PATH=$(echo "$RESPONSE" | jq -r .download_url)
  echo "$BASE_URL$DOWNLOAD_PATH"
  unset -f f;
}; f'
```
