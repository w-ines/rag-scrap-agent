from supabase import create_client, Client
from dotenv import load_dotenv
from fastapi import UploadFile
import os
import pathlib

load_dotenv() 
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

# Try to initialize Supabase, but handle failures gracefully
supabase: Client = None
SUPABASE_AVAILABLE = False

try:
    if url and key:
        supabase = create_client(url, key)
        SUPABASE_AVAILABLE = True
        print("[supabase_store] Supabase client initialized successfully")
    else:
        print("[supabase_store] ⚠️  Supabase credentials not found in .env")
except Exception as e:
    print(f"[supabase_store] ⚠️  Failed to initialize Supabase: {e}")

# Local storage fallback directory
LOCAL_STORAGE_DIR = pathlib.Path(__file__).parent.parent.parent / "local_storage" / "uploads"
LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

def store_pdf(file: UploadFile):
    """
    Store PDF file. Tries Supabase first, falls back to local storage if unavailable.
    """
    path = f"{file.filename}"
    file_bytes = file.file.read()
    
    # Try Supabase first
    if SUPABASE_AVAILABLE and supabase:
        try:
            options = {
                "content-type": file.content_type or "application/octet-stream",
                "upsert": "true",
            }
            res = supabase.storage.from_("public-bucket").upload(path, file_bytes, options)
            print(f"[store_pdf] Uploaded to Supabase: {res}")
            return f"{url}/storage/v1/object/public/public-bucket/{file.filename}"
        except Exception as e:
            print(f"[store_pdf] ⚠️  Supabase upload failed: {e}")
            print("[store_pdf] Falling back to local storage...")
    
    # Fallback to local storage
    local_file_path = LOCAL_STORAGE_DIR / file.filename
    with open(local_file_path, "wb") as f:
        f.write(file_bytes)
    
    local_url = f"file://{local_file_path.absolute()}"
    print(f"[store_pdf] Stored locally: {local_url}")
    return local_url


def embedding_pdf(file: UploadFile):
  pass
