# Deterministic LLM + embedding stand-in for the end-to-end test (design §13.4). Not shipped.
FROM python:3.11-slim
WORKDIR /app
COPY server.py .
RUN useradd --uid 10001 stub
USER stub
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/models')"
CMD ["python", "-u", "server.py"]
