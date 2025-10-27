-- Setup script for RAG system with Supabase storage
-- Run this in Supabase SQL Editor

-- Table pour stocker les chunks de documents
CREATE TABLE IF NOT EXISTS file_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens INTEGER,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index pour améliorer les performances de recherche
CREATE INDEX IF NOT EXISTS idx_file_items_file_id ON file_items(file_id);
CREATE INDEX IF NOT EXISTS idx_file_items_created_at ON file_items(created_at);

-- Commentaires sur les colonnes
COMMENT ON TABLE file_items IS 'Stores document chunks for RAG retrieval';
COMMENT ON COLUMN file_items.file_id IS 'Unique identifier for the source document';
COMMENT ON COLUMN file_items.content IS 'Text content of the chunk';
COMMENT ON COLUMN file_items.tokens IS 'Estimated token count for the chunk';
COMMENT ON COLUMN file_items.metadata IS 'Additional metadata (filename, source, chunk_index, etc.)';
