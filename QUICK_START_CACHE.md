# ⚡ Quick Start: Cache + Streaming

**Temps d'installation:** 5 minutes  
**Gain de performance:** Jusqu'à **30,000x plus rapide** sur queries répétées

---

## 🚀 Installation Express

```bash
# 1. Installer les dépendances
pip install cachetools>=5.3.0 redis>=5.0.0

# 2. Activer le cache (ajouter à .env)
echo "CACHE_ENABLED=true" >> .env
echo "CACHE_TTL_SECONDS=3600" >> .env
echo "CACHE_MAX_SIZE=1000" >> .env

# 3. Démarrer le serveur
python main.py
```

**C'est tout!** Le cache est maintenant actif ✅

---

## 🧪 Test Rapide

```bash
# Terminal 1: Serveur
python main.py

# Terminal 2: Test
python test_cache.py
# Choisir option 1 (Full demo)
```

**Résultat attendu:**
```
First query (cache miss):  25.234s
Second query (cache hit):  0.001s
Time saved:                25.233s
Speedup:                   25234x faster ⚡
```

---

## 📊 Monitoring

```bash
# Voir les stats en temps réel
curl http://localhost:8000/cache/stats | jq

# Résultat:
{
  "hit_rate_percent": 78.95,
  "total_time_saved_seconds": 234.56,
  "cache_size": 12
}
```

---

## 🎯 Cas d'Usage

### Scénario 1: FAQ sur un document

```bash
# User 1 demande
curl -X POST http://localhost:8000/ask \
  -d '{"query": "Résume ce document"}' \
  -H "Content-Type: application/json"
# → 30s (cache miss)

# User 2 demande la même chose
curl -X POST http://localhost:8000/ask \
  -d '{"query": "Résume ce document"}' \
  -H "Content-Type: application/json"
# → 0.001s (cache hit) ⚡
```

**Gain:** 30s → 0.001s (30,000x plus rapide)

### Scénario 2: Chatbot avec questions répétées

```
10 users posent "Qui est l'auteur?"

Sans cache: 10 × 25s = 250s
Avec cache: 25s + (9 × 0.001s) = 25.009s

Gain: 10x plus rapide
```

---

## ⚙️ Configuration Avancée

### Ajuster le TTL (durée de vie)

```bash
# Documents statiques (rarement mis à jour)
CACHE_TTL_SECONDS=86400  # 24 heures

# Documents dynamiques (souvent mis à jour)
CACHE_TTL_SECONDS=600  # 10 minutes
```

### Augmenter la taille du cache

```bash
# Plus de queries en cache
CACHE_MAX_SIZE=2000  # 2000 au lieu de 1000
```

### Vider le cache après upload

```python
# Dans main.py, après upload
from huggingsmolagent.tools.query_cache import clear_cache

@app.post("/upload")
async def upload_pdf(file: UploadFile):
    # ... upload logic ...
    clear_cache()  # Vider le cache car nouveaux documents
```

---

## 🐛 Troubleshooting

### Cache ne fonctionne pas?

```bash
# Vérifier que le cache est activé
curl http://localhost:8000/cache/stats

# Devrait montrer "enabled": true
```

### Hit rate trop faible?

```bash
# Augmenter le TTL
CACHE_TTL_SECONDS=7200  # 2 heures

# Augmenter la taille
CACHE_MAX_SIZE=2000
```

### Résultats obsolètes?

```bash
# Vider le cache manuellement
curl -X POST http://localhost:8000/cache/clear
```

---

## 📈 Métriques Attendues

Après 24h d'utilisation:

| Métrique | Valeur Cible |
|----------|--------------|
| Hit Rate | > 50% |
| Time Saved | > 1000s |
| Cache Size | 50-200 entries |

**Si hit rate < 20%:** Vos queries sont trop variées (normal pour certains cas d'usage)

---

## ✅ Checklist

- [ ] `pip install cachetools redis` ✅
- [ ] `CACHE_ENABLED=true` dans `.env` ✅
- [ ] Serveur redémarré ✅
- [ ] Test avec `python test_cache.py` ✅
- [ ] Hit rate > 50% après quelques heures ✅

---

## 📚 Documentation Complète

- **Guide détaillé:** `CACHE_STREAMING_GUIDE.md`
- **Code source:** `huggingsmolagent/tools/query_cache.py`
- **Tests:** `test_cache.py`

---

**Prochaine étape:** Laisser tourner 24h et vérifier les métriques avec `/cache/stats`
