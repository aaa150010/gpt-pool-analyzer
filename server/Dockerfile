FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py account_policy.py analytics.py cost_ledger.py pixel_manager.py pixel_routes.py withdrawal_email.py withdrawal_routes.py withdrawal_service.py withdrawals.py ./
RUN mkdir -p /data

EXPOSE 8095
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8095"]
