FROM python:3.12-slim
RUN pip install --no-cache-dir pillow
COPY evaluation/latencia/latencia.py /app/latencia.py
CMD ["python", "-u", "/app/latencia.py"]
