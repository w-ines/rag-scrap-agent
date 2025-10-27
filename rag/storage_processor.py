"""
Storage Processing module for Supabase.
Handles file uploads with error handling and validation.
"""

import os
import logging
from typing import Optional
from fastapi import UploadFile
from supabase import create_client, Client
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Configuration du logging avancé
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)


class StorageResult(BaseModel):
    """Result model for storage operations"""
    success: bool
    file_url: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = {}


class StorageProcessor:
    """
    Storage processor for Supabase file uploads.
    Handles file validation, upload, and error recovery.
    """
    
    def __init__(self, bucket_name: str = "public-bucket"):
        """
        Initialize storage processor
        
        Args:
            bucket_name: Supabase storage bucket name
        """
        self.bucket_name = bucket_name
        
        # Get Supabase credentials
        url = os.getenv("SUPABASE_URL")
        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        anon_key = os.getenv("SUPABASE_KEY")
        
        if service_role_key:
            key = service_role_key
            logger.info("✅ Using SUPABASE_SERVICE_ROLE_KEY (bypasses RLS)")
        elif anon_key:
            key = anon_key
            logger.warning("⚠️  Using SUPABASE_KEY (anon) - may have RLS restrictions")
            logger.warning("⚠️  Add SUPABASE_SERVICE_ROLE_KEY to .env for full storage access")
        else:
            raise ValueError("Missing SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY in .env")
        
        self.supabase: Client = create_client(url, key)
        self.supabase_url = url
        
        logger.info(f"📦 StorageProcessor initialized (bucket={bucket_name})")

    def validate_file(self, file: UploadFile) -> tuple[bool, Optional[str]]:
        """
        Validate file before upload
        
        Args:
            file: UploadFile to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Check filename
            if not file.filename:
                return False, "Missing filename"
            
            # Check content type
            if not file.content_type:
                logger.warning(f"⚠️  No content type for {file.filename}, proceeding anyway")
            
            # Check file size (optional, can add limits here)
            file.file.seek(0, 2)  # Seek to end
            file_size = file.file.tell()
            file.file.seek(0)  # Reset to beginning
            
            if file_size == 0:
                return False, "File is empty"
            
            logger.debug(f"✅ File validation passed: {file.filename} ({file_size} bytes)")
            return True, None
            
        except Exception as e:
            logger.error(f"❌ File validation error: {str(e)}")
            return False, f"Validation error: {str(e)}"

    def store_file(self, file: UploadFile) -> StorageResult:
        """
        Store file in Supabase storage
        
        Args:
            file: UploadFile to upload
            
        Returns:
            StorageResult with file URL and metadata
        """
        try:
            logger.info(f"📤 Uploading file: {file.filename}")
            
            # Validate file
            is_valid, validation_error = self.validate_file(file)
            if not is_valid:
                return StorageResult(
                    success=False,
                    error=f"File validation failed: {validation_error}"
                )
            
            # Read file bytes
            file.file.seek(0)
            file_bytes = file.file.read()
            
            # Prepare upload options
            options = {
                "content-type": file.content_type or "application/octet-stream",
                "upsert": "true",  # Overwrite if exists
            }
            
            # Upload to Supabase
            path = file.filename
            response = self.supabase.storage.from_(self.bucket_name).upload(
                path,
                file_bytes,
                options
            )
            
            # Construct public URL
            file_url = f"{self.supabase_url}/storage/v1/object/public/{self.bucket_name}/{file.filename}"
            
            logger.info(f"✅ File uploaded successfully: {file_url}")
            
            return StorageResult(
                success=True,
                file_url=file_url,
                metadata={
                    "filename": file.filename,
                    "size": len(file_bytes),
                    "content_type": file.content_type,
                    "bucket": self.bucket_name,
                    "path": path
                }
            )
            
        except Exception as e:
            logger.error(f"❌ Upload error: {str(e)}")
            return StorageResult(
                success=False,
                error=f"Upload failed: {str(e)}"
            )

    def delete_file(self, filename: str) -> bool:
        """
        Delete file from Supabase storage
        
        Args:
            filename: Name of file to delete
            
        Returns:
            True if deleted successfully
        """
        try:
            self.supabase.storage.from_(self.bucket_name).remove([filename])
            logger.info(f"🗑️  Deleted file: {filename}")
            return True
        except Exception as e:
            logger.error(f"❌ Delete error: {str(e)}")
            return False

    def list_files(self) -> list:
        """
        List all files in the bucket
        
        Returns:
            List of file objects
        """
        try:
            files = self.supabase.storage.from_(self.bucket_name).list()
            logger.info(f"📋 Listed {len(files)} files")
            return files
        except Exception as e:
            logger.error(f"❌ List error: {str(e)}")
            return []
