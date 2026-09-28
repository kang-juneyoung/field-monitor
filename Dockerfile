FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fieldmon ./fieldmon
COPY sim ./sim
COPY tools ./tools
COPY data ./data
EXPOSE 8000 5600/udp 5601
CMD ["uvicorn", "fieldmon.app:app", "--host", "0.0.0.0", "--port", "8000"]
