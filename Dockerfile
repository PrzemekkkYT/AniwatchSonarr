FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Port dla Twojego API
EXPOSE 8080

CMD ["python", "main.py"]