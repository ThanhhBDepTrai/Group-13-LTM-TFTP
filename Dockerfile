FROM python:3.11-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -e . 2>/dev/null || true

ENV TFTP_ADMIN_PASSWORD=ChangeMe123!

EXPOSE 10000

CMD ["python", "render_server.py"]
