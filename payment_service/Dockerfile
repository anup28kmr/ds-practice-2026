FROM python:3.11
WORKDIR /app
COPY ./payment_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
CMD python utils/other/hotreload.py "payment_service/src/app.py"
