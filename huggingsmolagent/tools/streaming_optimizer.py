"""
Streaming optimisé pour améliorer la perception de vitesse
Envoie les premiers résultats immédiatement pendant que l'agent continue à travailler
"""

import asyncio
import json
from typing import AsyncGenerator, Dict, Any, List
from queue import Queue
import threading
import time


class StreamingOptimizer:
    """
    Optimise le streaming pour donner une impression de rapidité.
    
    Stratégies:
    1. Early streaming: Envoie les premiers chunks dès qu'ils sont disponibles
    2. Progressive loading: Affiche les résultats partiels
    3. Chunked responses: Découpe les longues réponses
    """
    
    def __init__(self):
        self.buffer = Queue()
        self.is_streaming = False
    
    async def stream_with_preview(
        self,
        generator: AsyncGenerator,
        preview_chunks: int = 3
    ) -> AsyncGenerator[str, None]:
        """
        Stream avec preview immédiat des premiers chunks.
        
        Args:
            generator: Générateur asynchrone original
            preview_chunks: Nombre de chunks à envoyer immédiatement
        
        Yields:
            Chunks JSON formatés
        """
        chunks_sent = 0
        buffer = []
        
        async for chunk in generator:
            if chunks_sent < preview_chunks:
                # Envoyer immédiatement les premiers chunks
                yield self._format_chunk(chunk, is_preview=True)
                chunks_sent += 1
            else:
                # Buffer les chunks suivants
                buffer.append(chunk)
        
        # Envoyer le reste du buffer
        for chunk in buffer:
            yield self._format_chunk(chunk, is_preview=False)
    
    def _format_chunk(self, chunk: Any, is_preview: bool = False) -> str:
        """Formate un chunk pour le streaming"""
        data = {
            "chunk": chunk,
            "is_preview": is_preview,
            "timestamp": time.time()
        }
        return f"data: {json.dumps(data)}\n\n"
    
    async def stream_with_thinking_indicator(
        self,
        generator: AsyncGenerator,
        thinking_interval: float = 0.5
    ) -> AsyncGenerator[str, None]:
        """
        Ajoute des indicateurs "thinking" pendant les pauses.
        Améliore la perception de réactivité.
        """
        last_chunk_time = time.time()
        thinking_task = None
        
        async def send_thinking():
            while True:
                await asyncio.sleep(thinking_interval)
                if time.time() - last_chunk_time > thinking_interval:
                    yield self._format_thinking()
        
        async for chunk in generator:
            last_chunk_time = time.time()
            yield self._format_chunk(chunk)
    
    def _format_thinking(self) -> str:
        """Formate un indicateur de réflexion"""
        data = {
            "type": "thinking",
            "message": "🤔 Processing...",
            "timestamp": time.time()
        }
        return f"data: {json.dumps(data)}\n\n"
    
    async def stream_progressive_results(
        self,
        results: List[Dict[str, Any]],
        chunk_size: int = 1
    ) -> AsyncGenerator[str, None]:
        """
        Stream les résultats progressivement au lieu d'attendre la fin.
        
        Args:
            results: Liste de résultats à streamer
            chunk_size: Nombre de résultats par chunk
        
        Yields:
            Chunks de résultats
        """
        for i in range(0, len(results), chunk_size):
            chunk = results[i:i + chunk_size]
            yield self._format_results_chunk(chunk, i, len(results))
            await asyncio.sleep(0.01)  # Petit délai pour éviter la surcharge
    
    def _format_results_chunk(
        self,
        chunk: List[Dict[str, Any]],
        current_index: int,
        total: int
    ) -> str:
        """Formate un chunk de résultats"""
        data = {
            "type": "results",
            "chunk": chunk,
            "progress": {
                "current": current_index + len(chunk),
                "total": total,
                "percentage": round((current_index + len(chunk)) / total * 100, 1)
            },
            "timestamp": time.time()
        }
        return f"data: {json.dumps(data)}\n\n"


class ChunkedResponseGenerator:
    """
    Génère des réponses en chunks pour améliorer la perception de vitesse.
    Utile pour les longues réponses.
    """
    
    @staticmethod
    def chunk_text(text: str, chunk_size: int = 100) -> List[str]:
        """
        Découpe un texte en chunks de taille raisonnable.
        Essaie de couper aux limites de phrases.
        """
        if len(text) <= chunk_size:
            return [text]
        
        chunks = []
        current_chunk = ""
        
        # Découper par phrases
        sentences = text.replace('. ', '.|').replace('! ', '!|').replace('? ', '?|').split('|')
        
        for sentence in sentences:
            if len(current_chunk) + len(sentence) <= chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    @staticmethod
    async def stream_chunked_response(
        text: str,
        chunk_size: int = 100,
        delay: float = 0.05
    ) -> AsyncGenerator[str, None]:
        """
        Stream une réponse en chunks avec délai.
        Donne l'impression d'une génération en temps réel.
        """
        chunks = ChunkedResponseGenerator.chunk_text(text, chunk_size)
        
        for i, chunk in enumerate(chunks):
            data = {
                "type": "text_chunk",
                "content": chunk,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "is_final": i == len(chunks) - 1,
                "timestamp": time.time()
            }
            yield f"data: {json.dumps(data)}\n\n"
            
            if i < len(chunks) - 1:
                await asyncio.sleep(delay)


class ParallelStreamProcessor:
    """
    Traite plusieurs streams en parallèle et les combine.
    Utile pour afficher les résultats de plusieurs sources simultanément.
    """
    
    @staticmethod
    async def merge_streams(
        *generators: AsyncGenerator
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Fusionne plusieurs générateurs asynchrones.
        Envoie les chunks dès qu'ils sont disponibles, quelle que soit la source.
        """
        tasks = [asyncio.create_task(gen.__anext__()) for gen in generators]
        active_tasks = set(tasks)
        
        while active_tasks:
            done, pending = await asyncio.wait(
                active_tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in done:
                try:
                    result = task.result()
                    yield result
                    
                    # Relancer la tâche pour le prochain chunk
                    # (simplifié - en production, gérer StopAsyncIteration)
                except StopAsyncIteration:
                    pass
                
                active_tasks.discard(task)


# Exemple d'utilisation
async def example_optimized_streaming():
    """Exemple d'utilisation du streaming optimisé"""
    
    # Simuler une réponse longue
    long_response = """
    Voici une réponse très longue qui sera streamée en chunks pour améliorer
    l'expérience utilisateur. Au lieu d'attendre que toute la réponse soit générée,
    l'utilisateur verra les premiers mots apparaître immédiatement. Cela donne
    une impression de rapidité même si le temps total reste le même.
    """
    
    # Stream avec chunks
    async for chunk in ChunkedResponseGenerator.stream_chunked_response(
        long_response,
        chunk_size=50,
        delay=0.1
    ):
        print(chunk, end='', flush=True)


if __name__ == "__main__":
    # Test
    asyncio.run(example_optimized_streaming())
