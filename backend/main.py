from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import os
import shutil
from datetime import datetime, timedelta
import json
from pathlib import Path

app = FastAPI(title="Raspberry Pi Image API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create directories
UPLOAD_DIR = "uploads"
STATIC_DIR = "static"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# In-memory storage for device status (in production, use a database)
device_status = {}
image_metadata = []

class DeviceStatus(BaseModel):
    device_id: str
    status: str
    last_seen: Optional[datetime] = None

class ImageMetadata(BaseModel):
    device_id: str
    filename: str
    upload_time: datetime
    file_path: str

class HeartbeatRequest(BaseModel):
    device_id: str
    status: str

class CleanupRequest(BaseModel):
    device_id: str

@app.get("/")
async def root():
    return {"message": "Raspberry Pi Image API is running"}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now()}

@app.post("/api/heartbeat")
async def heartbeat(request: HeartbeatRequest):
    """Update device status when receiving heartbeat"""
    device_status[request.device_id] = {
        "status": request.status,
        "last_seen": datetime.now()
    }
    return {"message": "Heartbeat received", "device_id": request.device_id}

@app.get("/api/devices")
async def get_devices():
    """Get all devices and their status"""
    # Clean up old devices (offline for more than 5 minutes)
    cutoff_time = datetime.now() - timedelta(minutes=5)
    devices_to_remove = []
    
    for device_id, status in device_status.items():
        if status["status"] == "offline" and status["last_seen"] < cutoff_time:
            devices_to_remove.append(device_id)
    
    for device_id in devices_to_remove:
        del device_status[device_id]
    
    return {"devices": device_status}

@app.post("/api/upload-image")
async def upload_image(image: UploadFile = File(...), device_id: str = None):
    """Upload image from Raspberry Pi"""
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")
    
    # Generate filename with device ID and timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{device_id}-{timestamp}.png"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    # Save the uploaded file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
    
    # Store metadata
    metadata = ImageMetadata(
        device_id=device_id,
        filename=filename,
        upload_time=datetime.now(),
        file_path=file_path
    )
    image_metadata.append(metadata)
    
    # Keep only last 20 images per device
    device_images = [img for img in image_metadata if img.device_id == device_id]
    if len(device_images) > 20:
        # Remove oldest images
        device_images.sort(key=lambda x: x.upload_time)
        for old_img in device_images[:-20]:
            try:
                os.remove(old_img.file_path)
                image_metadata.remove(old_img)
            except FileNotFoundError:
                pass
    
    return {"message": "Image uploaded successfully", "filename": filename}

@app.get("/api/images/{device_id}")
async def get_device_images(device_id: str, limit: int = 20):
    """Get images for a specific device"""
    device_images = [img for img in image_metadata if img.device_id == device_id]
    device_images.sort(key=lambda x: x.upload_time, reverse=True)
    
    # Return metadata with static URLs
    images_with_urls = []
    for img in device_images[:limit]:
        images_with_urls.append({
            "filename": img.filename,
            "upload_time": img.upload_time,
            "url": f"/static/{img.filename}"
        })
    
    return {"images": images_with_urls}

@app.get("/api/images/{device_id}/latest")
async def get_latest_image(device_id: str):
    """Get the latest image for a device"""
    device_images = [img for img in image_metadata if img.device_id == device_id]
    if not device_images:
        raise HTTPException(status_code=404, detail="No images found for device")
    
    latest_image = max(device_images, key=lambda x: x.upload_time)
    return {
        "filename": latest_image.filename,
        "upload_time": latest_image.upload_time,
        "url": f"/static/{latest_image.filename}"
    }

@app.post("/api/cleanup-orphaned")
async def cleanup_orphaned(request: CleanupRequest):
    """Clean up orphaned images for a device"""
    device_id = request.device_id
    
    # Find images that exist on server but not in metadata
    server_files = set(os.listdir(UPLOAD_DIR))
    metadata_files = set(img.filename for img in image_metadata if img.device_id == device_id)
    
    orphaned_files = server_files - metadata_files
    removed_count = 0
    
    for filename in orphaned_files:
        if filename.startswith(f"{device_id}-"):
            try:
                os.remove(os.path.join(UPLOAD_DIR, filename))
                removed_count += 1
            except FileNotFoundError:
                pass
    
    return {"message": f"Cleaned up {removed_count} orphaned files", "removed_count": removed_count}

@app.get("/api/status")
async def get_system_status():
    """Get overall system status"""
    online_devices = sum(1 for status in device_status.values() if status["status"] == "online")
    total_images = len(image_metadata)
    
    return {
        "online_devices": online_devices,
        "total_devices": len(device_status),
        "total_images": total_images,
        "timestamp": datetime.now()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
