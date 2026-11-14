FROM python:3.12-slim
RUN pip install --no-cache-dir pillow
COPY latencia_bench/latencia.py /app/latencia.py
CMD ["python", "-u", "/app/latencia.py"]
