-- ============================================================================
-- FIX: Recréer la fonction match_documents avec la signature correcte
-- ============================================================================
-- Cette fonction est compatible avec LangChain SupabaseVectorStore
-- ============================================================================

-- Supprimer l'ancienne fonction si elle existe
DROP FUNCTION IF EXISTS match_documents(vector, int, jsonb);
DROP FUNCTION IF EXISTS match_documents(vector, float, int);

-- Créer la nouvelle fonction avec la signature correcte pour LangChain
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding VECTOR(1024),
    filter JSONB DEFAULT '{}'::jsonb
)
RETURNS TABLE (
    id UUID,
    content TEXT,
    metadata JSONB,
    embedding VECTOR(1024),
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        documents.id,
        documents.content,
        documents.metadata,
        documents.embedding,
        1 - (documents.embedding <=> query_embedding) AS similarity
    FROM documents
    -- Le filtre est appliqué via PostgREST, pas ici
    ORDER BY documents.embedding <=> query_embedding;
    -- La limite est appliquée via PostgREST avec .params.set("limit", k)
END;
$$;

-- Grant permissions
GRANT EXECUTE ON FUNCTION match_documents TO anon, authenticated;

-- ============================================================================
-- TEST: Vérifier que la fonction fonctionne
-- ============================================================================

-- Test avec un vecteur dummy
SELECT id, metadata->>'filename' as filename, similarity
FROM match_documents(
    array_fill(0.1, ARRAY[1024])::vector,  -- Dummy embedding
    '{}'::jsonb  -- Pas de filtre
)
LIMIT 5;

-- Vérifier la signature
SELECT 
    routine_name,
    routine_type,
    r.data_type as return_type,
    array_agg(parameter_name || ': ' || p.data_type ORDER BY ordinal_position) as parameters
FROM information_schema.routines r
LEFT JOIN information_schema.parameters p 
    ON r.specific_name = p.specific_name
WHERE routine_name = 'match_documents'
    AND routine_schema = 'public'
GROUP BY routine_name, routine_type, r.data_type;
