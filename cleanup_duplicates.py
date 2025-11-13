#!/usr/bin/env python3
"""
Script to identify and optionally remove duplicate documents from the vector store.
Duplicates are identified by having the same filename and similar chunk counts.
"""
import os
from dotenv import load_dotenv
from huggingsmolagent.tools.supabase_store import supabase
from huggingsmolagent.tools.vector_store import delete_document_by_doc_id
from collections import defaultdict

load_dotenv()


def find_duplicates(table_name: str = "documents"):
    """
    Find duplicate documents based on filename.
    
    Returns:
        Dict mapping filename to list of (doc_id, chunk_count, source) tuples
    """
    print("=" * 60)
    print("FINDING DUPLICATE DOCUMENTS")
    print("=" * 60)
    
    try:
        # Get all unique doc_ids with their metadata
        response = supabase.table(table_name).select("metadata").execute()
        
        # Group by filename
        files_by_name = defaultdict(list)
        
        for row in response.data:
            metadata = row.get("metadata", {})
            filename = metadata.get("filename")
            doc_id = metadata.get("doc_id")
            source = metadata.get("source", "")
            
            if filename and doc_id:
                files_by_name[filename].append({
                    "doc_id": doc_id,
                    "source": source,
                    "file_hash": metadata.get("file_hash", "N/A")
                })
        
        # Find duplicates (files with multiple doc_ids)
        duplicates = {}
        for filename, docs in files_by_name.items():
            # Get unique doc_ids
            unique_docs = {}
            for doc in docs:
                if doc["doc_id"] not in unique_docs:
                    unique_docs[doc["doc_id"]] = doc
            
            if len(unique_docs) > 1:
                # Count chunks for each doc_id
                doc_list = []
                for doc_id, doc_info in unique_docs.items():
                    count_response = supabase.table(table_name).select("id", count="exact").eq("metadata->>doc_id", doc_id).execute()
                    doc_list.append({
                        "doc_id": doc_id,
                        "chunks": count_response.count or 0,
                        "source": doc_info["source"],
                        "file_hash": doc_info["file_hash"]
                    })
                
                duplicates[filename] = sorted(doc_list, key=lambda x: x["chunks"], reverse=True)
        
        return duplicates
    
    except Exception as e:
        print(f"❌ Error finding duplicates: {e}")
        return {}


def display_duplicates(duplicates):
    """Display duplicate documents in a readable format."""
    if not duplicates:
        print("\n✅ No duplicates found!")
        return
    
    print(f"\n⚠️  Found {len(duplicates)} files with duplicates:\n")
    
    for filename, docs in duplicates.items():
        print(f"📄 {filename}")
        print(f"   Total versions: {len(docs)}")
        for i, doc in enumerate(docs, 1):
            marker = "✓ KEEP" if i == 1 else "✗ DELETE"
            print(f"   {marker} doc_id: {doc['doc_id'][:16]}... | chunks: {doc['chunks']} | hash: {doc['file_hash'][:16] if doc['file_hash'] != 'N/A' else 'N/A'}...")
        print()


def cleanup_duplicates(duplicates, dry_run=True):
    """
    Remove duplicate documents, keeping only the one with the most chunks.
    
    Args:
        duplicates: Dict from find_duplicates()
        dry_run: If True, only show what would be deleted
    """
    if not duplicates:
        print("\n✅ No duplicates to clean up!")
        return
    
    print("=" * 60)
    print("CLEANUP DUPLICATES" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)
    
    total_to_delete = 0
    total_chunks_to_delete = 0
    
    for filename, docs in duplicates.items():
        # Keep the first one (has most chunks), delete the rest
        to_keep = docs[0]
        to_delete = docs[1:]
        
        print(f"\n📄 {filename}")
        print(f"   ✓ Keeping: doc_id={to_keep['doc_id'][:16]}... ({to_keep['chunks']} chunks)")
        
        for doc in to_delete:
            total_to_delete += 1
            total_chunks_to_delete += doc['chunks']
            
            if dry_run:
                print(f"   ✗ Would delete: doc_id={doc['doc_id'][:16]}... ({doc['chunks']} chunks)")
            else:
                print(f"   ✗ Deleting: doc_id={doc['doc_id'][:16]}... ({doc['chunks']} chunks)")
                deleted = delete_document_by_doc_id(doc['doc_id'])
                print(f"      Deleted {deleted} chunks")
    
    print("\n" + "=" * 60)
    print(f"Summary: {total_to_delete} duplicate document(s) with {total_chunks_to_delete} total chunks")
    if dry_run:
        print("This was a DRY RUN. No changes were made.")
        print("Run with --execute to actually delete duplicates.")
    else:
        print("✅ Cleanup complete!")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    
    # Find duplicates
    duplicates = find_duplicates()
    display_duplicates(duplicates)
    
    # Check if user wants to execute cleanup
    execute = "--execute" in sys.argv or "-e" in sys.argv
    
    if duplicates:
        print("\n" + "=" * 60)
        if execute:
            confirm = input("⚠️  Are you sure you want to DELETE duplicates? (yes/no): ")
            if confirm.lower() == "yes":
                cleanup_duplicates(duplicates, dry_run=False)
            else:
                print("Cancelled.")
        else:
            cleanup_duplicates(duplicates, dry_run=True)
