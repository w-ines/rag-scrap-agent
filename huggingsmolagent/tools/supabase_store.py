from supabase import create_client, Client
from dotenv import load_dotenv
from fastapi import UploadFile
import os
load_dotenv() 
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)
print("supabase", supabase)

def store_pdf(file: UploadFile):
    path = f"{file.filename}"
    # Read file bytes from the UploadFile and upload as binary content
    file_bytes = file.file.read()
    options = {
        "content-type": file.content_type or "application/octet-stream",
        "upsert": "true",
    }
    res = supabase.storage.from_("public-bucket").upload(path, file_bytes, options)
    print("res", res)
    return f"{url}/storage/v1/object/public/public-bucket/{file.filename}"


def embedding_pdf(file: UploadFile):
  pass
