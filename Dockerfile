FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 1000 appuser

COPY --chown=appuser:appuser app.py rag.py wasalt.py catalog.py search_questions.py search_plan.py mongo_store.py preflight.py llm.py prompts.py styles.css chat_scroll.js ./
COPY --chown=appuser:appuser .streamlit/config.toml ./.streamlit/config.toml
COPY --chown=appuser:appuser data/ ./data/
USER appuser

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
